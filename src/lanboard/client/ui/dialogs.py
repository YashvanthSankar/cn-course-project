"""Smaller dialogs used by the main window.

* :class:`SavesDialog`: server-side named saves. Save the current board under a
  name (``controller.save``), list existing saves (``list_saves`` replies through
  ``saves_ready``) and, for the owner, load one (``controller.load``).
* :class:`RoomSettingsDialog`: owner-only room name, password and
  "editors can clear"; returns only the keys that changed for ``set_room(**kw)``.
* :func:`ask_password`: prompt used for ``net.password_required``.
* :func:`export_dialog`: file dialog for PNG / SVG / PDF export.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from . import theme


class SavesDialog(QDialog):
    """Save / load named boards stored on the server."""

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Saved boards")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        title = QLabel("Saved boards")
        title.setProperty("role", "h2")
        lay.addWidget(title)
        sub = QLabel("Saves are stored on the server, so everyone in the room can see them.")
        sub.setProperty("role", "hint")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        row = QHBoxLayout()
        self.name = QLineEdit(time.strftime("board-%Y%m%d-%H%M"))
        self.name.setPlaceholderText("Name for this save")
        self.name.setMaxLength(64)
        row.addWidget(self.name, 1)
        self.save_btn = QPushButton("Save current board")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.clicked.connect(self._save)
        if (getattr(controller, "me", None) or {}).get("role") == "viewer":
            self.save_btn.setEnabled(False)
            self.save_btn.setToolTip("Viewers cannot save boards")
        row.addWidget(self.save_btn)
        lay.addLayout(row)

        self.list = QListWidget()
        self.list.setMinimumHeight(200)
        self.list.itemDoubleClicked.connect(lambda *_: self._load())
        lay.addWidget(self.list, 1)
        self.status = QLabel("Loading saves…")
        self.status.setProperty("role", "muted")
        lay.addWidget(self.status)

        btns = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.setIcon(theme.icon("refresh", theme.tokens()["text2"], 16))
        refresh.clicked.connect(self.controller.list_saves)
        btns.addWidget(refresh)
        btns.addStretch(1)
        self.load_btn = QPushButton("Load selected")
        self.load_btn.setEnabled(False)
        owner = bool(controller.is_owner())
        self.load_btn.setToolTip("Replace the board for everyone" if owner else "Only the room owner can load")
        self.load_btn.clicked.connect(self._load)
        btns.addWidget(self.load_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)
        self.list.currentItemChanged.connect(lambda cur, _p: self.load_btn.setEnabled(owner and cur is not None))

        controller.saves_ready.connect(self.populate)
        controller.list_saves()

    def populate(self, saves: list) -> None:
        self.list.clear()
        for s in sorted(saves or [], key=lambda s: s.get("ts", 0), reverse=True):
            ts = s.get("ts", 0) or 0
            ts = ts / 1000 if ts > 1e12 else ts
            when = time.strftime("%d %b %H:%M", time.localtime(ts)) if ts else ""
            it = QListWidgetItem(theme.icon("save", theme.tokens()["text2"], 16),
                                 f"{s.get('name')}    ·  {s.get('objects', 0)} objects  ·  {when}")
            it.setData(Qt.ItemDataRole.UserRole, s.get("name"))
            self.list.addItem(it)
        self.status.setText(f"{len(saves or [])} save(s) on the server" if saves else "No saves yet.")

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.status.setText("Give the save a name first.")
            return
        self.controller.save(name)
        self.status.setText(f"Saved “{name}”.")

    def _load(self) -> None:
        it = self.list.currentItem()
        if it is None or not self.controller.is_owner():
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(self, "Load board", f"Replace the live board for everyone with “{name}”?") \
                == QMessageBox.StandardButton.Yes:
            self.controller.load(name)
            self.accept()


class RoomSettingsDialog(QDialog):
    """Owner settings. :meth:`changes` gives kwargs for ``controller.set_room``."""

    def __init__(self, room: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.room = dict(room or {})
        self.setWindowTitle("Room settings")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        title = QLabel(f"Room settings · {self.room.get('code', '')}")
        title.setProperty("role", "h2")
        lay.addWidget(title)
        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(self.room.get("name") or "")
        self.name.setMaxLength(64)
        form.addRow("Room name", self.name)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("leave empty to keep current" if self.room.get("protected")
                                         else "no password (open room)")
        form.addRow("New password", self.password)
        self.clear_pw = QCheckBox("Remove password (anyone with the code can join)")
        self.clear_pw.setEnabled(bool(self.room.get("protected")))
        self.clear_pw.toggled.connect(lambda on: self.password.setEnabled(not on))
        form.addRow("", self.clear_pw)
        self.editors_clear = QCheckBox("Editors may clear the board")
        self.editors_clear.setChecked(bool(self.room.get("editors_can_clear")))
        form.addRow("", self.editors_clear)
        self.locked = QCheckBox("Lock board (only the owner can draw)")
        self.locked.setChecked(bool(self.room.get("locked")))
        form.addRow("", self.locked)
        lay.addLayout(form)
        note = QLabel("Passwords never cross the network: the client sends a salted scrypt verifier, and "
                      "joiners prove knowledge of it with an HMAC challenge.")
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def changes(self) -> dict:
        out: dict = {}
        name = self.name.text().strip()
        if name and name != (self.room.get("name") or ""):
            out["name"] = name
        if self.clear_pw.isChecked():
            out["password"] = None
        elif self.password.text():
            out["password"] = self.password.text()
        if self.editors_clear.isChecked() != bool(self.room.get("editors_can_clear")):
            out["editors_can_clear"] = self.editors_clear.isChecked()
        if self.locked.isChecked() != bool(self.room.get("locked")):
            out["locked"] = self.locked.isChecked()
        return out


def ask_password(parent: QWidget, room_code: str, wrong: bool) -> str | None:
    """Ask for a room password. Returns None when the user cancels."""
    msg = (f"<b>Wrong password</b> for room {room_code}. Try again:" if wrong
           else f"Room <b>{room_code}</b> is protected. Enter its password:")
    text, ok = QInputDialog.getText(parent, "Password required", msg, QLineEdit.EchoMode.Password)
    return text if ok else None


EXPORT_FILTERS = {"PNG image (*.png)": ".png", "SVG vector (*.svg)": ".svg", "PDF document (*.pdf)": ".pdf"}


def export_dialog(parent: QWidget, default_name: str = "board") -> str | None:
    """Ask where to export. Adds the extension of the chosen filter if missing."""
    path, chosen = QFileDialog.getSaveFileName(parent, "Export board", f"{default_name}.png",
                                               ";;".join(EXPORT_FILTERS))
    if not path:
        return None
    if not path.lower().endswith((".png", ".svg", ".pdf")):
        path += EXPORT_FILTERS.get(chosen, ".png")
    return path

