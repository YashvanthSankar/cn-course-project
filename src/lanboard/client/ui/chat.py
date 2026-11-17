"""Chat tab: room chat as bubbles with timestamps, coloured names and system notices.

* ``chat_reset(list)`` replaces the whole transcript (JOINED tail / resume).
* ``chat_added(dict)`` appends one Chat ``{id, user_id, username, color, ts, text,
  kind: "user"|"system"}``. Messages are de-duplicated by ``id`` so a resend after a
  reconnect never shows twice.
* Rendering: a ``QListView`` over :class:`ChatModel` with :class:`BubbleDelegate`
  painting each message. My messages are right-aligned gradient bubbles, others
  are left-aligned with a colour avatar and the sender's name; consecutive
  messages from the same person within two minutes are grouped (no repeated
  header). System notices are small centred pills. Text is painted as plain
  text, so chat can never inject markup. The view only auto-scrolls when the
  user is already at the bottom, so reading old messages is not interrupted.
* ``unread_changed(int)`` lets the main window badge the tab while it is hidden.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QListView, QPushButton,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from lanboard.common import config as C

from . import theme
from .topbar import readable_on

MSG_ROLE = Qt.ItemDataRole.UserRole + 1
GROUP_SECONDS = 120


def format_time(ts) -> str:
    """``HH:MM`` from a unix timestamp in seconds (milliseconds also accepted)."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = time.time()
    if ts > 1e12:
        ts /= 1000.0
    return time.strftime("%H:%M", time.localtime(ts))


def _secs(ts) -> float:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return 0.0
    return ts / 1000.0 if ts > 1e12 else ts


class ChatModel(QAbstractListModel):
    """The transcript. Each row knows whether it continues the previous sender's group."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[dict] = []
        self.me_id: str | None = None

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        m = self.rows[index.row()]
        if role == MSG_ROLE:
            return m
        if role == Qt.ItemDataRole.DisplayRole:
            return str(m.get("text", ""))
        return None

    def _decorate(self, m: dict, prev: dict | None) -> dict:
        d = dict(m)
        d["mine"] = bool(self.me_id) and d.get("user_id") == self.me_id and d.get("kind") != "system"
        d["cont"] = (prev is not None and prev.get("kind") != "system" and d.get("kind") != "system"
                     and prev.get("user_id") == d.get("user_id")
                     and abs(_secs(d.get("ts")) - _secs(prev.get("ts"))) < GROUP_SECONDS)
        return d

    def reset_rows(self, msgs: list[dict]) -> None:
        self.beginResetModel()
        self.rows = []
        for m in msgs:
            self.rows.append(self._decorate(m, self.rows[-1] if self.rows else None))
        self.endResetModel()

    def append(self, m: dict) -> None:
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self.rows.append(self._decorate(m, self.rows[-1] if self.rows else None))
        self.endInsertRows()

    def trim(self, keep: int) -> None:
        extra = len(self.rows) - keep
        if extra > 0:
            self.beginRemoveRows(QModelIndex(), 0, extra - 1)
            del self.rows[:extra]
            self.endRemoveRows()


class BubbleDelegate(QStyledItemDelegate):
    """Paints chat bubbles. Sizes are computed from the wrapped text height."""

    AV = 28
    PAD_X, PAD_Y = 12, 8
    MAX_FRAC = 0.78

    def __init__(self, view: QListView) -> None:
        super().__init__(view)
        self.view = view
        self.body_font = theme.ui_font(13)
        self.name_font = theme.ui_font(12, QFont.Weight.DemiBold)
        self.meta_font = theme.ui_font(10)
        self.sys_font = theme.ui_font(11, QFont.Weight.Medium)

    def _text_rect(self, text: str, max_w: int) -> QRectF:
        fm = QFontMetrics(self.body_font)
        br = fm.boundingRect(0, 0, max(40, max_w), 10000, int(Qt.TextFlag.TextWordWrap), text)
        return QRectF(0, 0, br.width() + 1, br.height())

    def _max_text_w(self) -> int:
        w = self.view.viewport().width()
        return int(w * self.MAX_FRAC) - self.AV - 2 * self.PAD_X

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        m = index.data(MSG_ROLE) or {}
        w = self.view.viewport().width()
        if m.get("kind") == "system":
            return QSize(w, 30)
        tr = self._text_rect(str(m.get("text", "")), self._max_text_w())
        head = 0 if (m.get("cont") or m.get("mine")) else 18
        top = 3 if m.get("cont") else 8
        return QSize(w, int(top + head + tr.height() + 2 * self.PAD_Y + 2))

    def paint(self, p: QPainter, option, index) -> None:
        t = theme.tokens()
        m = index.data(MSG_ROLE) or {}
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = str(m.get("text", ""))
        stamp = format_time(m.get("ts"))
        if m.get("kind") == "system":
            p.setFont(self.sys_font)
            fm = p.fontMetrics()
            label = f"{text}  ·  {stamp}"
            label = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(r.width() - 40))
            w = fm.horizontalAdvance(label) + 22
            pill = QRectF(r.center().x() - w / 2, r.top() + 5, w, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["surface2"]))
            p.drawRoundedRect(pill, 10, 10)
            p.setPen(QColor(t["muted"]))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
            p.restore()
            return
        mine, cont = m.get("mine"), m.get("cont")
        top = r.top() + (3 if cont else 8)
        tr = self._text_rect(text, self._max_text_w())
        meta_w = QFontMetrics(self.meta_font).horizontalAdvance(stamp) + 8
        bw = max(tr.width(), 24) + 2 * self.PAD_X + meta_w
        bw = min(bw, r.width() * self.MAX_FRAC + 10)
        user_col = QColor(m.get("color") or t["accent"])
        if mine:
            x = r.right() - 10 - bw
            bubble = QRectF(x, top, bw, tr.height() + 2 * self.PAD_Y)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent_gradient(bubble, t))
            p.drawRoundedRect(bubble, 14, 14)
            if not cont:
                p.drawRoundedRect(QRectF(bubble.right() - 14, bubble.top(), 14, 14), 3, 3)
            fg, meta = QColor("white"), QColor(255, 255, 255, 185)
        else:
            x = r.left() + 10 + self.AV + 8
            if not cont:
                av = QRectF(r.left() + 10, top, self.AV, self.AV)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(user_col)
                p.drawEllipse(av)
                p.setFont(theme.ui_font(12, QFont.Weight.Bold))
                p.setPen(readable_on(user_col))
                p.drawText(av, Qt.AlignmentFlag.AlignCenter, (str(m.get("username") or "?")[:1] or "?").upper())
                p.setFont(self.name_font)
                name_col = QColor(user_col)
                if theme.manager().dark and name_col.lightness() < 110:
                    name_col = name_col.lighter(160)
                p.setPen(name_col)
                p.drawText(QRectF(x + 2, top - 1, r.width() - x, 16), Qt.AlignmentFlag.AlignVCenter,
                           str(m.get("username") or m.get("user_id") or "?"))
                top += 18
            bubble = QRectF(x, top, bw, tr.height() + 2 * self.PAD_Y)
            p.setPen(QPen(QColor(t["border"]), 1))
            p.setBrush(QColor(t["surface2"]))
            p.drawRoundedRect(bubble, 14, 14)
            if not cont:
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(bubble.left() + 0.5, bubble.top() + 0.5, 14, 14), 3, 3)
            fg, meta = QColor(t["text"]), QColor(t["muted"])
        p.setFont(self.body_font)
        p.setPen(fg)
        p.drawText(QRectF(bubble.left() + self.PAD_X, bubble.top() + self.PAD_Y, tr.width() + 2, tr.height()),
                   int(Qt.TextFlag.TextWordWrap), text)
        p.setFont(self.meta_font)
        p.setPen(meta)
        p.drawText(QRectF(bubble.right() - meta_w - 8, bubble.bottom() - 18, meta_w, 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, stamp)
        p.restore()


class ChatPanel(QWidget):
    """Transcript + input line. Enter sends via ``controller.send_chat``."""

    unread_changed = Signal(int)

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.controller = controller
        self.messages: list[dict] = []
        self._ids: set = set()
        self.unread = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 10)
        lay.setSpacing(8)
        self.model = ChatModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.delegate = BubbleDelegate(self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setUniformItemSizes(False)
        self.view.setStyleSheet("QListView { border: none; background: transparent; }")
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message the room…")
        self.input.setMaxLength(C.MAX_CHAT_LEN)
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setProperty("role", "primary")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        lay.addLayout(row)

        controller.chat_added.connect(self.add_message)
        controller.chat_reset.connect(self.reset)
        controller.me_changed.connect(lambda *_: self._rerender())
        theme.on_theme_change(self, lambda: self.view.viewport().update())
        if getattr(controller, "chat", None):
            self.reset(list(controller.chat))

    def reset(self, messages: list) -> None:
        self.messages = []
        self._ids = set()
        for m in messages or []:
            self._remember(m)
        self._rerender()

    def add_message(self, msg: dict) -> None:
        if not self._remember(msg):
            return
        at_bottom = self._at_bottom()
        self.model.me_id = (self.controller.me or {}).get("user_id")
        self.model.append(msg)
        self.model.trim(C.CHAT_HISTORY_MAX)
        if at_bottom or self._is_mine(msg):
            self._scroll_to_bottom()
        if not self.isVisible():
            self.unread += 1
            self.unread_changed.emit(self.unread)

    def _remember(self, msg: dict) -> bool:
        mid = msg.get("id")
        if mid is not None and mid in self._ids:
            return False
        if mid is not None:
            self._ids.add(mid)
        self.messages.append(msg)
        if len(self.messages) > C.CHAT_HISTORY_MAX:
            self.messages = self.messages[-C.CHAT_HISTORY_MAX:]
        return True

    def _is_mine(self, m: dict) -> bool:
        me = self.controller.me or {}
        return bool(me) and me.get("user_id") == m.get("user_id") and m.get("kind") != "system"

    def _rerender(self) -> None:
        self.model.me_id = (self.controller.me or {}).get("user_id")
        self.model.reset_rows(self.messages)
        self._scroll_to_bottom()

    def _at_bottom(self) -> bool:
        sb = self.view.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        self.view.scrollToBottom()

    def plain_text(self) -> str:
        """The transcript as text: ``[HH:MM] name: text`` per line (used by tests and copy)."""
        lines = []
        for m in self.messages:
            stamp = format_time(m.get("ts"))
            if m.get("kind") == "system":
                lines.append(f"[{stamp}] {m.get('text', '')}")
            else:
                lines.append(f"[{stamp}] {m.get('username') or m.get('user_id') or '?'}: {m.get('text', '')}")
        return "\n".join(lines)

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.controller.send_chat(text)
        self.input.clear()

    def focus_input(self) -> None:
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        if self.unread:
            self.unread = 0
            self.unread_changed.emit(0)
        self._scroll_to_bottom()
