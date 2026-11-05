"""Participants tab: who is in the room, their role, presence and raised hands.

Data comes from ``controller.participants`` (``{user_id: Participant}``, where a
Participant is ``{user_id, username, color, role, online, hand}``) and is re-read
on every ``participants_changed`` / ``me_changed`` / ``room_changed``. Rows are
painted by a delegate so each one shows a colour dot, the name, "(you)", a role
badge and a hand indicator without nesting widgets (fast for big classes).

The right-click menu offers Follow and Spotlight to everyone (both only change
the local view) and, for the owner, Make editor/viewer, Make presenter and Kick.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import theme

ROLE_ORDER = {"owner": 0, "editor": 1, "viewer": 2}
UID_ROLE = Qt.ItemDataRole.UserRole
DATA_ROLE = Qt.ItemDataRole.UserRole + 1


class _ParticipantDelegate(QStyledItemDelegate):
    """Paints one participant row."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), 40)

    def paint(self, p: QPainter, option, index) -> None:
        t = theme.tokens()
        d: dict = index.data(DATA_ROLE) or {}
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["accent_soft"]))
            p.drawRoundedRect(r, 7, 7)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["surface2"]))
            p.drawRoundedRect(r, 7, 7)

        online = d.get("online", True)
        col = QColor(d.get("color") or "#888")
        # colour avatar: initial on a filled circle (outlined when offline)
        av = QRectF(r.left() + 8, r.center().y() - 13, 26, 26)
        if online:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
        else:
            p.setPen(QPen(col, 2))
            p.setBrush(QColor(t["surface"]))
        p.drawEllipse(av)
        f = QFont(option.font)
        f.setBold(True)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("white") if online else col)
        p.drawText(av, Qt.AlignmentFlag.AlignCenter, (d.get("username") or "?")[:1].upper())
        # presence dot on the avatar
        p.setPen(QPen(QColor(t["surface"]), 2))
        p.setBrush(QColor(t["success"] if online else t["muted"]))
        p.drawEllipse(QRectF(av.right() - 8, av.bottom() - 8, 9, 9))

        x = av.right() + 10
        right = r.right() - 8
        # hand indicator (rightmost)
        if d.get("hand"):
            hand_r = QRectF(right - 22, r.center().y() - 11, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["warn_soft"]))
            p.drawRoundedRect(hand_r, 6, 6)
            theme.icon("hand", t["warn"], 16).paint(p, hand_r.adjusted(3, 3, -3, -3).toRect())
            right = hand_r.left() - 6
        if d.get("following"):
            fr = QRectF(right - 18, r.center().y() - 9, 18, 18)
            theme.icon("follow", t["accent"], 16).paint(p, fr.toRect())
            right = fr.left() - 4
        if d.get("spotlight"):
            sr = QRectF(right - 18, r.center().y() - 9, 18, 18)
            theme.icon("spotlight", t["accent"], 16).paint(p, sr.toRect())
            right = sr.left() - 4
        # role badge
        role = d.get("role", "viewer")
        if d.get("presenter"):
            role_text = f"{role} · presenting"
        else:
            role_text = role
        bf = QFont(option.font)
        bf.setPixelSize(10)
        bf.setBold(True)
        p.setFont(bf)
        bw = p.fontMetrics().horizontalAdvance(role_text.upper()) + 14
        badge = QRectF(right - bw, r.center().y() - 9, bw, 18)
        bg, fg = {"owner": (t["accent_soft"], t["accent"]),
                  "editor": (t["success_soft"], t["success"])}.get(role, (t["neutral_soft"], t["text2"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(badge, 9, 9)
        p.setPen(QColor(fg))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, role_text.upper())
        # name + "(you)"
        nf = QFont(option.font)
        nf.setPixelSize(13)
        nf.setWeight(QFont.Weight.DemiBold if d.get("me") else QFont.Weight.Normal)
        p.setFont(nf)
        name = d.get("username") or d.get("user_id", "?")
        if d.get("me"):
            name += "  (you)"
        p.setPen(QColor(t["text"] if online else t["muted"]))
        name_rect = QRectF(x, r.top(), badge.left() - x - 6, r.height() * 0.62)
        p.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(name_rect.width())))
        sf = QFont(option.font)
        sf.setPixelSize(11)
        p.setFont(sf)
        p.setPen(QColor(t["muted"]))
        status = "online" if online else "offline · may reconnect"
        p.drawText(QRectF(x, r.top() + r.height() * 0.6, badge.left() - x - 6, r.height() * 0.4),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, status)
        p.restore()


class ParticipantsPanel(QWidget):
    """The participant list with its context menu."""

    unread_hands = Signal(int)   # number of raised hands (for the tab badge)

    def __init__(self, controller, canvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.controller = controller
        self.canvas = canvas
        self.following: str | None = None
        self.spotlight: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel("In this room")
        self.title.setProperty("role", "h2")
        head.addWidget(self.title)
        head.addStretch(1)
        self.count = QLabel("0")
        self.count.setProperty("role", "muted")
        head.addWidget(self.count)
        lay.addLayout(head)

        self.list = QListWidget()
        self.list.setItemDelegate(_ParticipantDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(lambda it: self.follow(it.data(UID_ROLE)))
        self.list.setStyleSheet("QListWidget { border: none; }")
        lay.addWidget(self.list, 1)
        hint = QLabel("Right-click someone to follow their view or spotlight their work.")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        controller.participants_changed.connect(self.refresh)
        controller.me_changed.connect(lambda *_: self.refresh())
        controller.room_changed.connect(lambda *_: self.refresh())
        theme.manager().changed.connect(lambda *_: self.list.viewport().update())
        self.refresh()

    # -- data ---------------------------------------------------------------
    def _rows(self) -> list[dict]:
        ctl = self.controller
        me_id = (ctl.me or {}).get("user_id")
        room = ctl.room or {}
        rows = []
        for uid, part in (ctl.participants or {}).items():
            d = dict(part)
            d.setdefault("user_id", uid)
            d["me"] = uid == me_id
            d["presenter"] = room.get("presenter") == uid
            d["following"] = uid == self.following
            d["spotlight"] = uid == self.spotlight
            rows.append(d)
        rows.sort(key=lambda d: (not d.get("online", True), ROLE_ORDER.get(d.get("role"), 3),
                                 not d["me"], (d.get("username") or "").lower()))
        return rows

    def refresh(self) -> None:
        """Rebuild the list from the controller (cheap: one item per participant)."""
        rows = self._rows()
        selected = self.list.currentItem().data(UID_ROLE) if self.list.currentItem() else None
        self.list.clear()
        for d in rows:
            it = QListWidgetItem()
            it.setData(UID_ROLE, d["user_id"])
            it.setData(DATA_ROLE, d)
            it.setText(d.get("username") or d["user_id"])        # for accessibility / tests
            it.setToolTip(f"{d.get('username')} — {d.get('role')}"
                          f"{' — raised hand' if d.get('hand') else ''}")
            self.list.addItem(it)
            if d["user_id"] == selected:
                self.list.setCurrentItem(it)
        online = sum(1 for d in rows if d.get("online", True))
        self.count.setText(f"{online} online · {len(rows)} total")
        self.unread_hands.emit(sum(1 for d in rows if d.get("hand")))

    def row_data(self) -> list[dict]:
        """The rendered rows (used by tests)."""
        return [self.list.item(i).data(DATA_ROLE) for i in range(self.list.count())]

    # -- actions --------------------------------------------------------------
    def follow(self, uid: str | None) -> None:
        me_id = (self.controller.me or {}).get("user_id")
        if uid == me_id:
            uid = None
        self.following = uid
        self.canvas.set_follow(uid)
        self.refresh()

    def set_spotlight(self, uid: str | None) -> None:
        self.spotlight = uid
        self.canvas.set_spotlight(uid)
        self.refresh()

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if it is None:
            return
        d = it.data(DATA_ROLE)
        uid = d["user_id"]
        t = theme.tokens()
        ctl = self.controller
        m = QMenu(self)
        if not d["me"]:
            if self.following == uid:
                m.addAction(theme.icon("follow", t["text2"], 16), "Stop following", lambda: self.follow(None))
            else:
                m.addAction(theme.icon("follow", t["text2"], 16), f"Follow {d.get('username')}'s view",
                            lambda: self.follow(uid))
        if self.spotlight == uid:
            m.addAction(theme.icon("spotlight", t["text2"], 16), "Clear spotlight", lambda: self.set_spotlight(None))
        else:
            m.addAction(theme.icon("spotlight", t["text2"], 16), "Spotlight their work",
                        lambda: self.set_spotlight(uid))
        if ctl.is_owner():
            m.addSeparator()
            role = d.get("role")
            room = ctl.room or {}
            if not d["me"]:
                a = m.addAction(theme.icon("pen", t["text2"], 16), "Make editor", lambda: ctl.set_role(uid, "editor"))
                a.setEnabled(role != "editor")
                a = m.addAction(theme.icon("eye", t["text2"], 16), "Make viewer", lambda: ctl.set_role(uid, "viewer"))
                a.setEnabled(role != "viewer")
            if room.get("presenter") == uid:
                m.addAction(theme.icon("present", t["text2"], 16), "Stop presenter role",
                            lambda: ctl.set_room(presenter=None))
            else:
                m.addAction(theme.icon("present", t["text2"], 16), "Make presenter",
                            lambda: ctl.set_room(presenter=uid))
            if not d["me"]:
                m.addSeparator()
                m.addAction(theme.icon("kick", t["danger"], 16), "Kick from room…", lambda: self._kick(d))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _kick(self, d: dict) -> None:
        name = d.get("username") or d["user_id"]
        if QMessageBox.question(self, "Kick participant",
                                f"Remove <b>{name}</b> from the room?<br>They will not be able to resume "
                                "their session.") == QMessageBox.StandardButton.Yes:
            self.controller.kick(d["user_id"])
