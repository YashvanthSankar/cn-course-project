"""Top bar: room identity, the consistency badge (S3) and session actions.

The bar is a ``QToolBar`` so it spans the whole window above the canvas and the
dock. The actions themselves (undo, lock, present...) are created by the main
window, so their keyboard shortcuts work everywhere. This module only lays out
the room title, the copyable room code and the sync badge.

Sync badge states come from ``controller.sync_status``
``{state, seq, hash, pending}``:

=========  ===================================  =====
state      text                                 colour
=========  ===================================  =====
synced     ✓ In sync · seq N                    green
pending    Syncing… (k pending)                 amber
healing    Mismatch — healing                   red
mismatch   Mismatch — healing                   red
offline    Offline — edits queued               grey
=========  ===================================  =====
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolBar, QToolButton, QWidget

from . import theme


def sync_text(status: dict) -> tuple[str, str]:
    """Map a ``sync_status`` dict to ``(badge state, badge text)``."""
    state = str(status.get("state", "offline"))
    seq = status.get("seq", 0)
    pending = int(status.get("pending", 0) or 0)
    if state == "synced":
        return "synced", f"✓ In sync · seq {seq}"
    if state == "pending":
        return "pending", f"Syncing… ({pending} pending)"
    if state in ("healing", "mismatch"):
        return state, "Mismatch — healing"
    text = "Offline — edits queued"
    if pending:
        text += f" ({pending})"
    return "offline", text


class TopBar(QToolBar):
    """The window's top toolbar. Call :meth:`set_room` and :meth:`set_sync`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Room", parent)
        self.setObjectName("TopBar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(20, 20))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.share_text = ""

        ident = QWidget()
        hl = QHBoxLayout(ident)
        hl.setContentsMargins(4, 0, 8, 0)
        hl.setSpacing(8)
        self.logo = QLabel()
        self.logo.setPixmap(theme.app_icon().pixmap(26, 26))
        hl.addWidget(self.logo)
        self.room_name = QLabel("Not connected")
        self.room_name.setObjectName("RoomName")
        hl.addWidget(self.room_name)
        self.room_code = QLabel("")
        self.room_code.setObjectName("RoomCode")
        self.room_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hl.addWidget(self.room_code)
        self.copy_btn = QToolButton()
        self.copy_btn.setToolTip("Copy the room code and server address")
        self.copy_btn.setIconSize(QSize(16, 16))
        self.copy_btn.clicked.connect(self.copy_code)
        hl.addWidget(self.copy_btn)
        self.lock_badge = QLabel()
        self.lock_badge.setToolTip("Board is locked: only the owner can draw")
        self.lock_badge.hide()
        hl.addWidget(self.lock_badge)
        hl.addSpacing(6)
        self.badge = QLabel()
        self.badge.setObjectName("SyncBadge")
        self.badge.setToolTip("The server broadcasts a board hash every few ops. "
                              "This client compares it with its own replica.")
        hl.addWidget(self.badge)
        self.addWidget(ident)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.spacer_action = self.addWidget(spacer)
        self.set_sync({"state": "offline", "pending": 0})
        self.retheme()
        theme.manager().changed.connect(lambda *_: self.retheme())

    def retheme(self) -> None:
        t = theme.tokens()
        self.copy_btn.setIcon(theme.icon("copy", t["text2"], 16))
        self.lock_badge.setPixmap(theme.icon("lock_small", t["warn"], 16).pixmap(16, 16))
        theme.repolish(self.badge)

    # -- room ---------------------------------------------------------------------
    def set_room(self, room: dict | None, host: str = "", port: int = 0, lan: list[str] | None = None) -> None:
        room = room or {}
        code = room.get("code", "")
        self.room_name.setText(room.get("name") or code or "Not connected")
        self.room_code.setText(code)
        self.room_code.setVisible(bool(code))
        self.copy_btn.setVisible(bool(code))
        self.lock_badge.setVisible(bool(room.get("locked")))
        addr = (lan[0] if lan else host) or host
        self.share_text = f"LANBoard room {code} at {addr}:{port}" if code else ""
        tip = f"Room code {code}"
        if lan:
            tip += "\nOthers on your network connect to:\n" + "\n".join(f"  {a}:{port}" for a in lan)
        elif host:
            tip += f"\nServer {host}:{port}"
        self.room_code.setToolTip(tip)

    def copy_code(self) -> None:
        if self.share_text:
            QGuiApplication.clipboard().setText(self.share_text)

    # -- sync badge -------------------------------------------------------------------
    def set_sync(self, status: dict) -> None:
        state, text = sync_text(status)
        self.badge.setText(text)
        if self.badge.property("state") != state:
            self.badge.setProperty("state", state)
            theme.repolish(self.badge)
        h = status.get("hash")
        self.badge.setToolTip((f"Board hash {h} at seq {status.get('seq', 0)}\n" if h else "") +
                              "The server broadcasts a board hash every few ops; this client compares it "
                              "with its own replica and heals with a snapshot on mismatch.")
