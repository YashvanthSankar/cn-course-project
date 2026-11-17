"""Top bar: room identity, the consistency badge (S3), people and session actions.

Left to right::

    [logo] Room name [K7Q-2MX ⧉] [lock]  (● In sync · seq 42)        [avatars +3] [Invite] | actions…

* The room-code chip copies "room code + server address" to the clipboard.
* :class:`SyncPill` is the Consistency Verifier badge: a pill with a coloured dot.
  While the replica is not confirmed in sync the dot sends out a soft pulse.
* :class:`AvatarStack` shows who is online, in their user colours, overlapping,
  with a "+N" chip when there are more than fit.
* The actions themselves (undo, lock, present...) are ``QAction`` objects created
  by the main window, so their keyboard shortcuts work everywhere. The bar only
  lays them out as buttons (:meth:`TopBar.add_action`).

Sync badge states come from ``controller.sync_status`` ``{state, seq, hash, pending}``:

=========  ===================================  =====
state      text                                 colour
=========  ===================================  =====
synced     In sync · seq N                      green
pending    Syncing · k pending                  amber
healing    Healing · resyncing                  blue
mismatch   Mismatch · healing                   blue
offline    Offline · N edits queued             red   (pending edits, after a join)
offline    Offline                              red   (nothing queued / never joined)
connecting Connecting… / Signing in…            blue  (before the first join or retrying)
=========  ===================================  =====

The main window adds ``conn`` (the connection state) and ``joined_once`` to
the dict so the offline case can tell "still connecting" from "dropped".
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
from .widgets import vsep

STATE_TOKEN = {"synced": "success", "pending": "warn", "healing": "info", "mismatch": "info",
               "offline": "danger", "connecting": "info"}


def sync_text(status: dict) -> tuple[str, str]:
    """Map a ``sync_status`` dict to ``(badge state, badge text)``."""
    state = str(status.get("state", "offline"))
    seq = status.get("seq", 0)
    pending = int(status.get("pending", 0) or 0)
    if state == "synced":
        return "synced", f"In sync · seq {seq}"
    if state == "pending":
        return "pending", f"Syncing · {pending} pending"
    if state == "healing":
        return "healing", "Healing · resyncing"
    if state == "mismatch":
        return "mismatch", "Mismatch · healing"
    conn = str(status.get("conn", "") or "")
    if pending and status.get("joined_once"):
        return "offline", f"Offline · {pending} edit{'s' if pending != 1 else ''} queued"
    if conn == "connecting":
        return "connecting", "Connecting…"
    if conn == "authenticating":
        return "connecting", "Signing in…"
    if conn == "reconnecting":
        return "connecting", "Reconnecting…"
    return "offline", "Offline"


def readable_on(bg: QColor) -> QColor:
    """Black or white text, whichever reads better on ``bg``."""
    lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor("#111522") if lum > 170 else QColor("white")


class SyncPill(QWidget):
    """The Consistency Verifier badge. ``text()`` and the ``state`` property mirror the label API."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self.setProperty("state", "offline")
        self._phase = 0.0
        self._pulse = QVariantAnimation(self, duration=1400, startValue=0.0, endValue=1.0)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pulse.valueChanged.connect(self._on_phase)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        theme.on_theme_change(self, self.update)

    def _on_phase(self, v) -> None:
        self._phase = float(v)
        self.update(0, 0, 30, self.height())

    def text(self) -> str:
        return self._text

    def set_state(self, state: str, text: str) -> None:
        self._text = text
        self.setProperty("state", state)
        self.updateGeometry()
        self.adjustSize()
        if state == "synced":
            self._pulse.stop()
            self._phase = 0.0
        elif self._pulse.state() != QVariantAnimation.State.Running and self.isVisible():
            self._pulse.start()
        self.update()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        if self.property("state") != "synced":
            self._pulse.start()

    def hideEvent(self, ev) -> None:  # noqa: N802
        super().hideEvent(ev)
        self._pulse.stop()

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(theme.ui_font(12, QFont.Weight.DemiBold))
        return QSize(fm.horizontalAdvance(self._text) + 38, 28)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        tok = STATE_TOKEN.get(str(self.property("state")), "danger")
        c = QColor(t[tok])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 1.5, -0.5, -1.5)
        p.setPen(QPen(theme.alpha(c, 0.35), 1))
        p.setBrush(theme.alpha(c, 0.13 if not theme.manager().dark else 0.16))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        cx, cy = r.left() + 14, r.center().y()
        if self._phase > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.alpha(c, 0.45 * (1 - self._phase)))
            rad = 3.5 + 6.5 * self._phase
            p.drawEllipse(QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(cx - 3.8, cy - 3.8, 7.6, 7.6))
        p.setFont(theme.ui_font(12, QFont.Weight.DemiBold))
        p.setPen(QColor(t["text"]) if theme.manager().dark else c.darker(135))
        p.drawText(QRectF(cx + 10, r.top(), r.width(), r.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()


class AvatarStack(QWidget):
    """Overlapping coloured avatars of the people online, with a "+N" overflow chip."""

    clicked = Signal()
    D, STEP, MAX = 30, 21, 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.people: list[dict] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self.D + 4)
        theme.on_theme_change(self, self.update)

    def set_people(self, people: list[dict]) -> None:
        self.people = [p for p in people if p.get("online", True)]
        names = ", ".join(p.get("username", "?") for p in self.people)
        self.setToolTip(f"<b>{len(self.people)} online</b><br>{names}" if self.people else "Nobody online yet")
        self.setFixedWidth(self._width())
        self.update()

    def _width(self) -> int:
        n = min(len(self.people), self.MAX) + (1 if len(self.people) > self.MAX else 0)
        return 4 + (self.D + self.STEP * (n - 1) if n else 0)

    def mousePressEvent(self, _ev) -> None:  # noqa: N802
        self.clicked.emit()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shown = self.people[: self.MAX]
        extra = len(self.people) - len(shown)
        x = 2.0
        f = theme.ui_font(12, QFont.Weight.Bold)
        p.setFont(f)
        for person in shown:
            r = QRectF(x, 2, self.D, self.D)
            col = QColor(person.get("color") or "#888")
            p.setPen(QPen(QColor(t["surface"]), 2.5))
            p.setBrush(col)
            p.drawEllipse(r)
            p.setPen(readable_on(col))
            name = (person.get("username") or "?").strip() or "?"
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, name[:1].upper())
            if person.get("hand"):
                p.setPen(QPen(QColor(t["surface"]), 1.5))
                p.setBrush(QColor(t["warn"]))
                p.drawEllipse(QRectF(r.right() - 9, r.top() - 1, 10, 10))
            x += self.STEP
        if extra > 0:
            r = QRectF(x, 2, self.D, self.D)
            p.setPen(QPen(QColor(t["surface"]), 2.5))
            p.setBrush(QColor(t["surface3"]))
            p.drawEllipse(r)
            p.setFont(theme.ui_font(11, QFont.Weight.Bold))
            p.setPen(QColor(t["text2"]))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"+{extra}")
        p.end()


class TopBar(QWidget):
    """The window's top bar. Call :meth:`set_room`, :meth:`set_sync` and :meth:`set_people`."""

    copied = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(54)
        self.share_text = ""
        self._buttons: list[QToolButton] = []

        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 0, 12, 0)
        hl.setSpacing(8)
        self.logo = QLabel()
        self.logo.setPixmap(theme.app_icon().pixmap(30, 30))
        hl.addWidget(self.logo)
        hl.addSpacing(2)

        ident = QVBoxLayout()
        ident.setSpacing(0)
        ident.setContentsMargins(0, 0, 0, 0)
        self.room_name = QLabel("Not connected")
        self.room_name.setObjectName("RoomName")
        ident.addWidget(self.room_name)
        self.room_sub = QLabel("")
        self.room_sub.setObjectName("RoomSub")
        ident.addWidget(self.room_sub)
        hl.addLayout(ident)

        self.room_code = QToolButton()
        self.room_code.setProperty("role", "chip")
        self.room_code.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.room_code.setIconSize(QSize(13, 13))
        self.room_code.setCursor(Qt.CursorShape.PointingHandCursor)
        self.room_code.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.room_code.clicked.connect(self.copy_code)
        hl.addWidget(self.room_code)
        self.copy_btn = self.room_code
        self.lock_badge = QLabel()
        self.lock_badge.setToolTip("Board is locked: only the owner can draw")
        self.lock_badge.hide()
        hl.addWidget(self.lock_badge)
        hl.addSpacing(4)
        self.badge = SyncPill()
        self.badge.setToolTip("The server broadcasts a board hash every few ops. "
                              "This client compares it with its own replica.")
        hl.addWidget(self.badge)
        hl.addStretch(1)

        self.avatars = AvatarStack()
        hl.addWidget(self.avatars)
        self.invite_btn = QPushButton("Invite")
        self.invite_btn.setProperty("role", "primary")
        self.invite_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.invite_btn.setIconSize(QSize(16, 16))
        self.invite_btn.setToolTip("Show the address and room code others need to join")
        hl.addWidget(self.invite_btn)
        hl.addSpacing(4)
        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(2)
        hl.addLayout(self.actions_row)

        self.set_sync({"state": "offline", "pending": 0})
        self.retheme()
        theme.on_theme_change(self, self.retheme)

    def add_action(self, act, text_beside: bool = False) -> QToolButton:
        b = QToolButton()
        b.setDefaultAction(act)
        b.setIconSize(QSize(19, 19))
        b.setFixedHeight(34)
        if not text_beside:
            b.setFixedWidth(34)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if text_beside:
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setProperty("textBeside", True)
            b.setProperty("canShowText", True)
        self.actions_row.addWidget(b)
        self._buttons.append(b)
        return b

    def add_menu_button(self, icon_name: str, tip: str, menu) -> QToolButton:
        b = QToolButton()
        b.setIconSize(QSize(19, 19))
        b.setFixedSize(34, 34)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setMenu(menu)
        b.setProperty("iconName", icon_name)
        self.actions_row.addWidget(b)
        self._buttons.append(b)
        self.retheme()
        return b

    def add_separator(self) -> None:
        self.actions_row.addSpacing(5)
        self.actions_row.addWidget(vsep(22), 0, Qt.AlignmentFlag.AlignVCenter)
        self.actions_row.addSpacing(5)

    def retheme(self) -> None:
        t = theme.tokens()
        self.room_code.setIcon(theme.icon("copy", t["muted"], 13))
        self.lock_badge.setPixmap(theme.icon("lock_small", t["warn"], 16).pixmap(16, 16))
        self.invite_btn.setIcon(theme.icon("invite", "#ffffff", 16))
        for b in self._buttons:
            name = b.property("iconName")
            if name:
                b.setIcon(theme.icon(name, t["text2"], 19))
        self.badge.update()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        """Compact form below ~1200 px: icon-only Present, no address line, fewer avatars."""
        super().resizeEvent(ev)
        compact = self.width() < 1200
        if compact != getattr(self, "_compact", None):
            self._compact = compact
            self.room_sub.setVisible(not compact)
            self.avatars.MAX = 3 if compact else 5
            self.avatars.set_people(self.avatars.people)
            for b in self._buttons:
                if b.property("canShowText"):
                    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                                         else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                    b.setProperty("textBeside", not compact)
                    theme.repolish(b)
                    if compact:
                        b.setFixedWidth(34)
                    else:
                        b.setMinimumWidth(0)
                        b.setMaximumWidth(16777215)
        self.room_name.setMaximumWidth(160 if compact else 260)

    def set_people(self, people: list[dict]) -> None:
        self.avatars.set_people(people)

    def set_room(self, room: dict | None, host: str = "", port: int = 0, lan: list[str] | None = None) -> None:
        room = room or {}
        code = room.get("code", "")
        self.room_name.setText(room.get("name") or code or "Not connected")
        self.room_code.setText(code)
        self.room_code.setVisible(bool(code))
        self.lock_badge.setVisible(bool(room.get("locked")))
        addr = (lan[0] if lan else host) or host
        self.share_text = f"LANBoard room {code} at {addr}:{port}" if code else ""
        self.room_sub.setText((f"hosting on {addr}:{port}" if lan else f"{addr}:{port}") if addr else "")
        tip = f"Room code {code} (click to copy)"
        if lan:
            tip += "\nOthers on your network connect to:\n" + "\n".join(f"  {a}:{port}" for a in lan)
        elif host:
            tip += f"\nServer {host}:{port}"
        self.room_code.setToolTip(tip)

    def copy_code(self) -> None:
        if self.share_text:
            QGuiApplication.clipboard().setText(self.share_text)
            self.copied.emit(self.share_text)

    def set_sync(self, status: dict) -> None:
        state, text = sync_text(status)
        self.badge.set_state(state, text)
        h = status.get("hash")
        self.badge.setToolTip((f"Board hash {h} at seq {status.get('seq', 0)}\n" if h else "") +
                              "The server broadcasts a board hash every few ops; this client compares it "
                              "with its own replica and heals with a snapshot on mismatch.")
