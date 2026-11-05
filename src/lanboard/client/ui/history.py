"""History tab (S6 Time-lapse Replay): scrub and replay the whole session.

``controller.request_history()`` makes the server reply with
``HISTORY {base_seq, base:{objects}, ops:[{seq, user_id, op_id, ts, op}]}``
which the controller re-emits as ``history_ready``.

:class:`HistoryTimeline` turns that payload into boards. Position ``i`` means
"the base board plus the first ``i`` ops", so position 0 is the base and
position ``len(ops)`` is the latest state. Boards are rebuilt by replaying the
ops with the same deterministic :meth:`Board.apply` the live replicas use, so
the preview is exactly what everyone saw at that seq. To keep scrubbing fast a
copy of the board is cached every :data:`CHECKPOINT_EVERY` ops, so any position
costs at most 199 op applications plus one copy.

The panel shows the result through ``canvas.show_preview_board(board)`` (read-only)
and gives it back with ``show_preview_board(None)``. "Restore to here" (owner only)
submits a ``replace`` op holding every object of the preview board, tombstones
included, so undo/redo history stays meaningful after the restore.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from lanboard.common.model import Board

from . import theme
from .widgets import Segmented

CHECKPOINT_EVERY = 200
TICK_MS = 50                         # playback timer; 1x = 1 op per tick = 20 ops/s
SPEEDS = (1, 4, 16)


class HistoryTimeline:
    """Replays a HISTORY payload into boards at any position, with checkpoints."""

    def __init__(self, payload: dict, checkpoint_every: int = CHECKPOINT_EVERY) -> None:
        self.every = max(1, int(checkpoint_every))
        self.base_seq = int(payload.get("base_seq", 0) or 0)
        base = payload.get("base") or {}
        self.ops: list[dict] = sorted((o for o in payload.get("ops", []) if isinstance(o, dict) and "op" in o),
                                      key=lambda o: o.get("seq", 0))
        base_board = Board.from_snapshot({"seq": self.base_seq, "objects": base.get("objects", [])})
        self.checkpoints: dict[int, Board] = {0: base_board}
        self._work = base_board.copy()       # board being advanced during playback
        self._work_pos = 0

    def __len__(self) -> int:
        return len(self.ops)

    @property
    def end(self) -> int:
        return len(self.ops)

    def seq_at(self, pos: int) -> int:
        """The server seq the board at ``pos`` corresponds to."""
        pos = max(0, min(pos, self.end))
        return self.base_seq if pos == 0 else int(self.ops[pos - 1].get("seq", 0))

    def entry(self, pos: int) -> dict | None:
        """The op entry that produced position ``pos`` (None for the base)."""
        return self.ops[pos - 1] if 0 < pos <= self.end else None

    def _advance(self, board: Board, start: int, stop: int) -> None:
        for i in range(start, stop):
            entry = self.ops[i]
            try:
                board.apply(entry["op"], int(entry.get("seq", 0)))
            except Exception:                 # a malformed op is skipped, like on replicas
                pass
            nxt = i + 1
            if nxt % self.every == 0 and nxt not in self.checkpoints:
                self.checkpoints[nxt] = board.copy()

    def board_at(self, pos: int) -> Board:
        """A fresh, independent board at position ``pos`` (safe to hand to the canvas)."""
        pos = max(0, min(int(pos), self.end))
        if not (self._work_pos <= pos and pos - self._work_pos < self.every):
            start = max(k for k in self.checkpoints if k <= pos)
            self._work = self.checkpoints[start].copy()
            self._work_pos = start
        self._advance(self._work, self._work_pos, pos)
        self._work_pos = pos
        return self._work.copy()


def describe_op(op: dict) -> str:
    """A short human description of an op for the timeline caption."""
    k = op.get("k")
    if k == "add":
        return f"added {op.get('obj', {}).get('type', 'object')}"
    if k == "update":
        keys = ", ".join(sorted(op.get("set", {})))
        return "moved" if keys == "off" else f"edited ({keys})"
    if k == "delete":
        return "cleared the board" if op.get("clear") else f"erased {len(op.get('ids', []))} object(s)"
    if k == "restore":
        return f"restored {len(op.get('ids', []))} object(s)"
    if k == "replace":
        return f"loaded a board ({len(op.get('objects', []))} objects)"
    if k == "batch":
        return f"batch of {len(op.get('ops', []))} changes"
    if k == "clear":
        return "cleared the board"
    return str(k)


class HistoryPanel(QWidget):
    """Scrubber + time-lapse player for the room's op log."""

    preview_changed = Signal(bool, int)      # (previewing?, seq)

    def __init__(self, controller, canvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.controller = controller
        self.canvas = canvas
        self.timeline: HistoryTimeline | None = None
        self.previewing = False
        self.speed = 1
        self.last_board: Board | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(10)
        title = QLabel("Time-lapse replay")
        title.setProperty("role", "h2")
        lay.addWidget(title)
        sub = QLabel("Every change is an ordered op in the server log. Replay them to watch the board "
                     "being built, or go back to any point.")
        sub.setProperty("role", "hint")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.load_btn = QPushButton("Load history")
        self.load_btn.setProperty("role", "primary")
        self.load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_btn.clicked.connect(self.request)
        lay.addWidget(self.load_btn)

        self.info = QLabel("No history loaded yet.")
        self.info.setProperty("role", "muted")
        lay.addWidget(self.info)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self.slider)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.play_btn = QToolButton()
        self.play_btn.setFixedSize(36, 36)
        self.play_btn.setEnabled(False)
        self.play_btn.setToolTip("Play / pause time-lapse")
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn)
        self.speed_seg = Segmented(["1×", "4×", "16×"])
        self.speed_seg.changed.connect(lambda i: setattr(self, "speed", SPEEDS[i]))
        row.addWidget(self.speed_seg, 1)
        lay.addLayout(row)

        self.pos_label = QLabel("")
        self.pos_label.setProperty("role", "metric")
        lay.addWidget(self.pos_label)
        self.op_label = QLabel("")
        self.op_label.setProperty("role", "hint")
        self.op_label.setWordWrap(True)
        lay.addWidget(self.op_label)

        btns = QHBoxLayout()
        self.exit_btn = QPushButton("Exit preview")
        self.exit_btn.setEnabled(False)
        self.exit_btn.clicked.connect(self.exit_preview)
        btns.addWidget(self.exit_btn)
        self.restore_btn = QPushButton("Restore to here")
        self.restore_btn.setEnabled(False)
        self.restore_btn.setToolTip("Owner only: make this the live board for everyone (undoable by loading again)")
        self.restore_btn.clicked.connect(self.restore_here)
        btns.addWidget(self.restore_btn)
        lay.addLayout(btns)
        lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

        controller.history_ready.connect(self.load_payload)
        theme.manager().changed.connect(lambda *_: self._update_play_icon())
        self._update_play_icon()

    # -- loading -------------------------------------------------------------
    def request(self) -> None:
        self.info.setText("Requesting the op log from the server…")
        self.load_btn.setEnabled(False)
        QTimer.singleShot(4000, lambda: self.load_btn.setEnabled(True))
        self.controller.request_history()

    def load_payload(self, payload: dict) -> None:
        self.stop()
        self.timeline = HistoryTimeline(payload)
        tl = self.timeline
        self.load_btn.setEnabled(True)
        self.load_btn.setText("Reload history")
        self.info.setText(f"{len(tl):,} ops · seq {tl.base_seq} → {tl.seq_at(tl.end)}")
        self.slider.blockSignals(True)
        self.slider.setRange(0, tl.end)
        self.slider.setValue(tl.end)
        self.slider.blockSignals(False)
        self.slider.setEnabled(tl.end > 0)
        self.play_btn.setEnabled(tl.end > 0)
        self._caption(tl.end)

    # -- preview ---------------------------------------------------------------
    def _on_slider(self, pos: int) -> None:
        self.show_position(pos)

    def show_position(self, pos: int) -> Board | None:
        """Build the board at ``pos`` and show it read-only on the canvas."""
        if self.timeline is None:
            return None
        board = self.timeline.board_at(pos)
        self.last_board = board
        self.canvas.show_preview_board(board)
        if not self.previewing:
            self.previewing = True
            self.exit_btn.setEnabled(True)
        self.restore_btn.setEnabled(bool(self.controller.is_owner()))
        self._caption(pos)
        self.preview_changed.emit(True, self.timeline.seq_at(pos))
        return board

    def _caption(self, pos: int) -> None:
        tl = self.timeline
        if tl is None:
            return
        self.pos_label.setText(f"seq {tl.seq_at(pos)}  ·  {pos}/{tl.end}")
        e = tl.entry(pos)
        if e is None:
            self.op_label.setText("Base snapshot (start of the kept log)")
            return
        who = e.get("user_id", "?")
        try:
            who = self.controller.name_of(who) or who
        except Exception:
            pass
        ts = e.get("ts")
        when = ""
        if isinstance(ts, (int, float)):
            when = time.strftime(" · %H:%M:%S", time.localtime(ts / 1000 if ts > 1e12 else ts))
        self.op_label.setText(f"{who} {describe_op(e.get('op', {}))}{when}")

    def exit_preview(self) -> None:
        self.stop()
        if self.previewing:
            self.previewing = False
            self.canvas.show_preview_board(None)
            self.preview_changed.emit(False, 0)
        self.exit_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)

    def restore_here(self) -> None:
        if self.timeline is None or not self.controller.is_owner():
            return
        pos = self.slider.value()
        seq = self.timeline.seq_at(pos)
        if QMessageBox.question(
                self, "Restore board",
                f"Replace the live board for everyone with the version at <b>seq {seq}</b>?") \
                != QMessageBox.StandardButton.Yes:
            return
        board = self.timeline.board_at(pos)
        objects = [dict(o) for o in board.objects.values()]   # live + tombstoned
        self.controller.submit({"k": "replace", "objects": objects}, undoable=False)
        self.exit_preview()

    # -- playback ------------------------------------------------------------------
    def toggle_play(self) -> None:
        if self.timer.isActive():
            self.stop()
        else:
            self.play()

    def play(self) -> None:
        if self.timeline is None or self.timeline.end == 0:
            return
        if self.slider.value() >= self.timeline.end:
            self.slider.setValue(0)              # restart from the beginning
        self.timer.start()
        self._update_play_icon()

    def stop(self) -> None:
        self.timer.stop()
        self._update_play_icon()

    def _tick(self) -> None:
        tl = self.timeline
        if tl is None:
            return self.stop()
        nxt = min(tl.end, self.slider.value() + self.speed)
        self.slider.setValue(nxt)
        if nxt >= tl.end:
            self.stop()

    def _update_play_icon(self) -> None:
        t = theme.tokens()
        self.play_btn.setIcon(theme.icon("pause" if self.timer.isActive() else "play", t["accent"], 20))
