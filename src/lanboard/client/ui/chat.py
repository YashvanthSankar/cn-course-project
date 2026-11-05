"""Chat tab: room chat with timestamps, coloured names and system notices.

* ``chat_reset(list)`` replaces the whole transcript (JOINED tail / resume).
* ``chat_added(dict)`` appends one Chat ``{id, user_id, username, color, ts, text,
  kind: "user"|"system"}``. Messages are de-duplicated by ``id`` so a resend after a
  reconnect never shows twice.
* Rendering uses a QTextBrowser with small HTML blocks (escaped, so chat text can
  never inject markup). The view only auto-scrolls when the user is already at
  the bottom, so reading old messages is not interrupted.
* ``unread_changed(int)`` lets the main window badge the tab while it is hidden.
"""

from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from lanboard.common import config as C

from . import theme


def format_time(ts) -> str:
    """``HH:MM`` from a unix timestamp in seconds (milliseconds also accepted)."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = time.time()
    if ts > 1e12:                       # milliseconds
        ts /= 1000.0
    return time.strftime("%H:%M", time.localtime(ts))


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
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        self.view.setStyleSheet("QTextBrowser { border: none; }")
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message the room…  (Enter to send)")
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
        theme.manager().changed.connect(lambda *_: self._rerender())
        if getattr(controller, "chat", None):
            self.reset(list(controller.chat))

    # -- inbound --------------------------------------------------------------
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
        old = self.view.verticalScrollBar().value()
        self.view.append(self._html(msg))           # append = new paragraph, O(1)
        if at_bottom or self._is_mine(msg):
            self._scroll_to_bottom()
        else:
            self.view.verticalScrollBar().setValue(old)
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

    # -- rendering --------------------------------------------------------------
    def _html(self, m: dict) -> str:
        t = theme.tokens()
        stamp = format_time(m.get("ts"))
        text = html.escape(str(m.get("text", ""))).replace("\n", "<br>")
        if m.get("kind") == "system":
            return (f"<div style='margin:3px 0; color:{t['muted']};'><i>[{stamp}] {text}</i></div>")
        name = html.escape(str(m.get("username") or m.get("user_id") or "?"))
        col = m.get("color") or t["text"]
        you = f" <span style='color:{t['muted']}'>(you)</span>" if self._is_mine(m) else ""
        return (f"<div style='margin:3px 0;'><span style='color:{t['muted']}'>[{stamp}]</span> "
                f"<b style='color:{col}'>{name}</b>{you}: "
                f"<span style='color:{t['text']}'>{text}</span></div>")

    def _is_mine(self, m: dict) -> bool:
        me = self.controller.me or {}
        return bool(me) and me.get("user_id") == m.get("user_id") and m.get("kind") != "system"

    def _rerender(self) -> None:
        self.view.setHtml("".join(self._html(m) for m in self.messages))
        self._scroll_to_bottom()

    def _at_bottom(self) -> bool:
        sb = self.view.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def plain_text(self) -> str:
        return self.view.toPlainText()

    # -- outbound ---------------------------------------------------------------
    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.controller.send_chat(text)
        self.input.clear()

    def focus_input(self) -> None:
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    # -- unread badge -------------------------------------------------------------
    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        if self.unread:
            self.unread = 0
            self.unread_changed.emit(0)
        self._scroll_to_bottom()
