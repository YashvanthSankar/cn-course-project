"""The LANBoard main window: wires the controller, the canvas and every panel together.

Layout::

    ┌──────────────────────────── TopBar (room · code · sync badge · actions) ─────────┐
    │ Tool │                                              │ People | Chat | History |   │
    │ rail │           CanvasView (+ banner/toast         │ Network (HUD, Inspector,   │
    │      │           overlays)                          │ Chaos Lab)   ← right dock  │
    └──────┴──────────────── status bar (state · server · me · RTT · seq · hash · zoom)┘

The window never touches sockets or the board model directly: it calls the
public ``SessionController`` / ``CanvasView`` methods (see docs/CLIENT_API.md) and
reacts to their signals. When the session must end (Leave, fatal error such as
KICKED, cancelled password prompt) it emits ``session_ended(reason, message)``
and the application shell (``client/__main__.py``) decides whether to show the
connect dialog again or quit.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .chat import ChatPanel
from .dialogs import RoomSettingsDialog, SavesDialog, ask_password, export_dialog
from .history import HistoryPanel
from .network_panel import NetworkPanel
from .participants import ParticipantsPanel
from .toolbar import ToolRail
from .topbar import TopBar
from .widgets import Banner, ToastHost

#: ERROR codes that end the session (mirrors NetClient's fatal set, plus connect failures)
FATAL_CODES = frozenset({"KICKED", "ROOM_NOT_FOUND", "ROOM_EXISTS", "PROTO_MISMATCH", "CONNECT_FAILED",
                         "SERVER_FULL", "EXPIRED"})
FATAL_TITLES = {
    "KICKED": "You were removed from the room",
    "ROOM_NOT_FOUND": "Room not found",
    "ROOM_EXISTS": "That room code is already taken",
    "PROTO_MISMATCH": "Incompatible LANBoard version",
    "CONNECT_FAILED": "Can't reach the server",
    "SERVER_FULL": "The server is full",
    "FORBIDDEN": "Not allowed to join",
}
STATE_LABELS = {"idle": "Idle", "connecting": "Connecting", "authenticating": "Authenticating",
                "joined": "Connected", "reconnecting": "Reconnecting", "closed": "Disconnected"}
STATE_TOKENS = {"joined": "success", "reconnecting": "warn", "connecting": "accent",
                "authenticating": "accent", "closed": "danger", "idle": "muted"}


def _safe(fn, default=False):
    """Call a controller predicate that may fail before JOINED (me/room still None)."""
    try:
        return fn()
    except Exception:
        return default


class MainWindow(QMainWindow):
    """Top-level window for one session (one room on one server)."""

    session_ended = Signal(str, str)        # (reason: "leave"|"fatal"|"quit", message)

    def __init__(self, controller, canvas=None, settings: QSettings | None = None,
                 lan_addresses: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.net = controller.net
        self.settings = settings or QSettings()
        self.lan_addresses = list(lan_addresses or [])
        self._conn_state = "idle"
        self._was_reconnecting = False
        self._ending = False
        self._hosting_announced = False
        self._can_undo = self._can_redo = False
        self._icon_actions: list[tuple[QAction, str, str | None]] = []
        self.setWindowIcon(theme.app_icon())
        self.setWindowTitle("LANBoard")
        self.setObjectName("MainWindow")
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks)

        if canvas is None:
            from lanboard.client.canvas.view import CanvasView      # imported late: heavy module
            canvas = CanvasView(controller)
        self.canvas = canvas

        self._build_central()
        self._build_actions()
        self._build_topbar()
        self._build_dock()
        self._build_statusbar()
        self._connect_signals()
        self._restore_settings()

        # initial state (the controller may already hold a room when the window is built late)
        if getattr(controller, "room", None):
            self.on_room_changed(controller.room)
        if getattr(controller, "me", None):
            self.on_me_changed(controller.me)
        self._update_permissions()
        self.retheme()
        theme.manager().changed.connect(lambda *_: self.retheme())

    # ------------------------------------------------------------------ building
    def _build_central(self) -> None:
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.rail = ToolRail(self.canvas)
        lay.addWidget(self.rail)
        self.stage = QWidget()                      # canvas + overlays
        sl = QVBoxLayout(self.stage)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.canvas)
        lay.addWidget(self.stage, 1)
        self.setCentralWidget(central)
        self.toasts = ToastHost(self.stage)
        self.banner = Banner(self.stage)
        self._banner_action = lambda: None
        self.banner.action.connect(lambda: self._banner_action())
        self.preview_banner = Banner(self.stage, offset=62)

    def _act(self, text: str, icon_name: str | None, slot, shortcut=None, checkable: bool = False,
             tip: str | None = None, on_color: str | None = None) -> QAction:
        act = QAction(text, self)
        act.setCheckable(checkable)
        if shortcut is not None:
            seqs = shortcut if isinstance(shortcut, list) else [shortcut]
            act.setShortcuts([QKeySequence(s) for s in seqs])
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        keys = ", ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in act.shortcuts())
        act.setToolTip(f"<b>{tip or text}</b>" + (f"&nbsp;&nbsp;<span>{keys}</span>" if keys else ""))
        if checkable:
            act.triggered.connect(slot)             # user clicks only; programmatic setChecked is silent
        else:
            act.triggered.connect(lambda _=False: slot())
        if icon_name:
            self._icon_actions.append((act, icon_name, on_color))
        self.addAction(act)                         # window-wide shortcut
        return act

    def _build_actions(self) -> None:
        c = self.canvas
        self.act_undo = self._act("Undo", "undo", self.controller.undo, "Ctrl+Z")
        self.act_redo = self._act("Redo", "redo", self.controller.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.act_clear = self._act("Clear board", "trash", self.confirm_clear,
                                   tip="Clear the board for everyone")
        self.act_lock = self._act("Lock board", "lock", self._on_lock, checkable=True,
                                  tip="Lock the board (owner only): only you can draw", on_color="accent")
        self.act_present = self._act("Present", "present", self._on_present, checkable=True,
                                     tip="Present: broadcast your view so others can follow it", on_color="accent")
        self.act_hand = self._act("Raise hand", "hand", self._on_hand, checkable=True,
                                  tip="Raise / lower your hand", on_color="warn")
        self.act_image = self._act("Insert image", "image", self.insert_image)
        self.act_pdf = self._act("Import PDF", "pdf", self.import_pdf, tip="Import PDF pages as images")
        self.act_export = self._act("Export", "export", self.export, "Ctrl+E", tip="Export PNG / SVG / PDF")
        self.act_saves = self._act("Saved boards", "folder", self.open_saves,
                                   tip="Save / load named boards on the server")
        self.act_save = self._act("Save board", None, self.quick_save, "Ctrl+S")
        self.act_settings = self._act("Room settings", "settings", self.open_room_settings,
                                      tip="Room settings (owner): name, password, permissions")
        self.act_theme = self._act("Toggle dark mode", "moon", self.toggle_theme)
        self.act_leave = self._act("Leave room", "leave", self.leave)
        # shortcut-only actions
        self._act("Delete selection", None, lambda: c.delete_selection(), ["Delete", "Backspace"])
        self._act("Select all", None, lambda: c.select_all(), "Ctrl+A")
        self._act("Reset zoom", None, lambda: c.zoom_reset(), "Ctrl+0")
        self._act("Fit board", None, lambda: c.zoom_fit(), "Ctrl+1")
        self._act("Full screen", None, self.toggle_fullscreen, "F11")
        self._act("Focus chat", None, self.focus_chat, ["Ctrl+Return", "Ctrl+Enter"])

    def _build_topbar(self) -> None:
        self.topbar = TopBar(self)
        tb = self.topbar
        for act in (self.act_undo, self.act_redo, self.act_clear):
            tb.addAction(act)
        tb.addSeparator()
        for act in (self.act_lock, self.act_present, self.act_hand):
            tb.addAction(act)
        tb.addSeparator()
        for act in (self.act_image, self.act_pdf, self.act_export, self.act_saves, self.act_settings):
            tb.addAction(act)
        tb.addSeparator()
        tb.addAction(self.act_theme)
        tb.addAction(self.act_leave)
        for act in (self.act_present, self.act_hand):
            w = tb.widgetForAction(act)
            if isinstance(w, QToolButton):
                w.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                w.setProperty("textBeside", True)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

    def _build_dock(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SideTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.tabBar().setUsesScrollButtons(False)   # all four tabs always visible
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.participants = ParticipantsPanel(self.controller, self.canvas)
        self.chat = ChatPanel(self.controller)
        self.history = HistoryPanel(self.controller, self.canvas)
        self.network = NetworkPanel(self.net, self.controller)
        self.TAB_PEOPLE = self.tabs.addTab(self.participants, "People")
        self.TAB_CHAT = self.tabs.addTab(self.chat, "Chat")
        self.TAB_HISTORY = self.tabs.addTab(self.history, "History")
        self.TAB_NET = self.tabs.addTab(self.network, "Network")
        self.dock = QDockWidget("Panels", self)
        self.dock.setObjectName("SideDock")
        self.dock.setWidget(self.tabs)
        self.dock.setTitleBarWidget(QWidget())        # cleaner: no title strip
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                              | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.dock.setMinimumWidth(380)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [420], Qt.Orientation.Horizontal)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.hover_label = QLabel("")
        sb.addWidget(self.hover_label, 1)
        self.status_labels: dict[str, QLabel] = {}
        for key, tip in (("state", "Connection state"), ("server", "Server address"),
                         ("me", "You and your role"), ("rtt", "Round-trip time (PING/PONG)"),
                         ("seq", "Last applied server sequence number"), ("hash", "Board hash (consistency check)"),
                         ("pending", "Your ops not yet confirmed by the server"), ("zoom", "Zoom")):
            lab = QLabel("")
            lab.setToolTip(tip)
            sb.addPermanentWidget(lab)
            self.status_labels[key] = lab
        self._set_status_state("idle")
        self.status_labels["rtt"].setText("RTT –")
        self.status_labels["seq"].setText("seq 0")
        self.status_labels["pending"].setText("0 pending")
        self.status_labels["zoom"].setText("100%")

    def _connect_signals(self) -> None:
        ctl, net, c = self.controller, self.net, self.canvas
        ctl.room_changed.connect(self.on_room_changed)
        ctl.me_changed.connect(self.on_me_changed)
        ctl.sync_status.connect(self.on_sync_status)
        ctl.undo_state.connect(self.on_undo_state)
        ctl.notice.connect(self.on_notice)
        ctl.connection_state.connect(self.on_connection_state)
        ctl.signal_received.connect(self.on_signal)
        ctl.participants_changed.connect(self._update_permissions)
        net.error.connect(self.on_net_error)
        net.password_required.connect(self.on_password_required)
        if hasattr(net, "rtt_changed"):
            net.rtt_changed.connect(self.on_rtt)
        if hasattr(net, "joined"):
            net.joined.connect(self.on_joined)
        if hasattr(c, "zoom_changed"):
            c.zoom_changed.connect(self.on_zoom)
        if hasattr(c, "hover_info"):
            c.hover_info.connect(self.hover_label.setText)
        self.chat.unread_changed.connect(self._on_unread)
        self.participants.unread_hands.connect(self._on_hands)
        self.history.preview_changed.connect(self._on_preview)
        self.preview_banner.action.connect(self.history.exit_preview)

    def _restore_settings(self) -> None:
        geo = self.settings.value("main/geometry")
        if isinstance(geo, (QByteArray, bytes)) and geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1440, 900)
        state = self.settings.value("main/state")
        if isinstance(state, (QByteArray, bytes)) and state:
            self.restoreState(state)

    def retheme(self) -> None:
        t = theme.tokens()
        for act, name, on in self._icon_actions:
            if act is self.act_theme:
                name = "sun" if theme.manager().dark else "moon"
            act.setIcon(theme.icon(name, t["text2"], 20, on_color=t[on] if on else None))
        for idx, name in ((self.TAB_PEOPLE, "users"), (self.TAB_CHAT, "chat"),
                          (self.TAB_HISTORY, "history"), (self.TAB_NET, "network")):
            self.tabs.setTabIcon(idx, theme.icon(name, t["text2"], 16))
        self._set_status_state(self._conn_state)

    # ------------------------------------------------------------------ controller slots
    def on_room_changed(self, room: dict) -> None:
        room = room or {}
        name, code = room.get("name") or room.get("code", ""), room.get("code", "")
        self.setWindowTitle(f"LANBoard — {name} ({code})" if code else "LANBoard")
        self.topbar.set_room(room, getattr(self.net, "host", ""), getattr(self.net, "port", 0), self.lan_addresses)
        self.act_lock.blockSignals(True)
        self.act_lock.setChecked(bool(room.get("locked")))
        self.act_lock.setText("Unlock board" if room.get("locked") else "Lock board")
        self.act_lock.blockSignals(False)
        presenter = room.get("presenter")
        me_id = (self.controller.me or {}).get("user_id")
        if presenter and presenter != me_id and presenter != getattr(self, "_last_presenter", None):
            who = _safe(lambda: self.controller.name_of(presenter), presenter)
            self.toasts.show(f"{who} is presenting. Right-click them in People and choose Follow.", "info")
        self._last_presenter = presenter
        self._update_permissions()

    def on_me_changed(self, me: dict) -> None:
        me = me or {}
        self.status_labels["me"].setText(f"{me.get('username', '?')} · {me.get('role', '?')}")
        self.act_hand.blockSignals(True)
        self.act_hand.setChecked(bool(me.get("hand")))
        self.act_hand.setText("Lower hand" if me.get("hand") else "Raise hand")
        self.act_hand.blockSignals(False)
        self._update_permissions()

    def on_sync_status(self, st: dict) -> None:
        self.topbar.set_sync(st)
        self.status_labels["seq"].setText(f"seq {st.get('seq', 0)}")
        h = st.get("hash")
        self.status_labels["hash"].setText(f"#{str(h)[:8]}" if h else "")
        self.status_labels["pending"].setText(f"{st.get('pending', 0)} pending")

    def on_undo_state(self, can_undo: bool, can_redo: bool) -> None:
        self._can_undo, self._can_redo = bool(can_undo), bool(can_redo)
        self._update_permissions()

    def on_notice(self, level: str, text: str) -> None:
        self.toasts.show(text, level)

    def on_signal(self, user_id: str, kind: str) -> None:
        me_id = (self.controller.me or {}).get("user_id")
        if kind == "hand_up" and user_id != me_id:
            who = _safe(lambda: self.controller.name_of(user_id), user_id)
            self.toasts.show(f"✋ {who} raised a hand", "warn")

    def on_connection_state(self, state: str) -> None:
        prev, self._conn_state = self._conn_state, state
        self._set_status_state(state)
        host = f"{getattr(self.net, 'host', '')}:{getattr(self.net, 'port', '')}"
        if state in ("connecting", "authenticating"):
            if prev == "reconnecting":
                return                          # keep the reconnect banner while retrying
            self._show_banner(f"Connecting to {host}…", "info", "Cancel", self.leave)
        elif state == "reconnecting":
            self._was_reconnecting = True
            self._show_banner("Connection lost — reconnecting… your edits are queued", "warn",
                              "Leave", self.leave)
        elif state == "joined":
            self.banner.hide()
            if self._was_reconnecting:
                self._was_reconnecting = False
                self.toasts.show("Reconnected. Missed changes were replayed.", "success")
        elif state == "closed" and not self._ending:
            self._show_banner("Disconnected from the server", "warn", "Back to start",
                              lambda: self._end("leave", ""))

    def _show_banner(self, text: str, kind: str, action_text: str, action) -> None:
        self._banner_action = action
        self.banner.show_message(text, kind, action_text)

    def _set_status_state(self, state: str) -> None:
        t = theme.tokens()
        col = t.get(STATE_TOKENS.get(state, "muted"), t["muted"])
        self.status_labels["state"].setText(f"<span style='color:{col}'>●</span> {STATE_LABELS.get(state, state)}")
        host, port = getattr(self.net, "host", ""), getattr(self.net, "port", "")
        if self.lan_addresses:
            self.status_labels["server"].setText(f"hosting · {self.lan_addresses[0]}:{port}")
        else:
            self.status_labels["server"].setText(f"{host}:{port}" if host else "")

    # ------------------------------------------------------------------ net slots
    def on_rtt(self, ms: float) -> None:
        self.status_labels["rtt"].setText(f"RTT {ms:.0f} ms")

    def on_zoom(self, z: float) -> None:
        pct = z * 100 if z <= 50 else z           # accept a factor (1.0) or a percentage
        self.status_labels["zoom"].setText(f"{pct:.0f}%")

    def on_joined(self, payload: dict) -> None:
        if self.lan_addresses and not self._hosting_announced:
            self._hosting_announced = True
            code = (payload.get("room") or {}).get("code", "")
            port = getattr(self.net, "port", "")
            self.toasts.show(f"You're hosting. Others join at {self.lan_addresses[0]}:{port}, room {code}",
                             "success", 9000)

    def on_net_error(self, code: str, msg: str) -> None:
        # NetClient switches to "closed" right after emitting a fatal error, so decide on the
        # next event-loop turn; known fatal codes are handled even if no state change follows.
        QTimer.singleShot(0, lambda: self._handle_error(code, msg))

    def _handle_error(self, code: str, msg: str) -> None:
        if self._ending:
            return
        # Non-fatal errors (RATE_LIMITED, FORBIDDEN...) are already surfaced by the
        # controller as a ``notice`` toast, so only fatal ones are handled here.
        if code in FATAL_CODES or self._conn_state == "closed":
            self._fatal(code, msg)

    def _fatal(self, code: str, msg: str) -> None:
        self._ending = True
        self.banner.hide()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("LANBoard")
        box.setText(f"<b>{FATAL_TITLES.get(code, 'Disconnected')}</b>")
        box.setInformativeText(msg or code)
        back = box.addButton("Back to start", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back)
        box.exec()
        self._end("fatal" if box.clickedButton() is back else "quit", f"{FATAL_TITLES.get(code, code)}: {msg}")

    def on_password_required(self, room_code: str, wrong: bool) -> None:
        pw = ask_password(self, room_code, wrong)
        if pw is None:
            self.leave(confirm=False)
        else:
            self.net.provide_password(pw)

    # ------------------------------------------------------------------ permissions
    def _update_permissions(self, *_args) -> None:
        ctl = self.controller
        can_edit = bool(_safe(ctl.can_edit))
        owner = bool(_safe(ctl.is_owner))
        can_clear = bool(_safe(ctl.can_clear))
        self.rail.set_can_edit(can_edit)
        self.act_undo.setEnabled(can_edit and self._can_undo)
        self.act_redo.setEnabled(can_edit and self._can_redo)
        self.act_clear.setEnabled(can_clear)
        self.act_lock.setEnabled(owner)
        self.act_settings.setEnabled(owner)
        self.act_image.setEnabled(can_edit)
        self.act_pdf.setEnabled(can_edit)

    # ------------------------------------------------------------------ action handlers
    def confirm_clear(self) -> None:
        if not _safe(self.controller.can_clear):
            return
        if QMessageBox.question(self, "Clear board",
                                "Clear the board for <b>everyone</b> in the room?<br>"
                                "(It can be undone, and every version stays in History.)") \
                == QMessageBox.StandardButton.Yes:
            self.controller.clear_board()

    def _on_lock(self, checked: bool) -> None:
        self.controller.set_room(locked=bool(checked))

    def _on_present(self, checked: bool) -> None:
        self.canvas.set_presenting(bool(checked))
        self.act_present.setText("Stop presenting" if checked else "Present")
        if checked:
            self.toasts.show("You are presenting. Others can follow your view from People.", "info")

    def _on_hand(self, checked: bool) -> None:
        self.controller.send_signal("hand_up" if checked else "hand_down")
        self.act_hand.setText("Lower hand" if checked else "Raise hand")

    def insert_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert image", "",
                                              "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if path and self.canvas.insert_image_file(path) is False:
            self.toasts.show("That image could not be inserted (unreadable or too large).", "error")

    def import_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF documents (*.pdf)")
        if path and self.canvas.import_pdf(path) is False:
            self.toasts.show("That PDF could not be imported.", "error")

    def export(self) -> None:
        code = (self.controller.room or {}).get("code", "board")
        path = export_dialog(self, f"lanboard-{code}")
        if path:
            try:
                ok = self.canvas.export(path)
                if ok is False:
                    self.toasts.show(f"Could not export to {path}", "error")
                else:
                    self.toasts.show(f"Exported to {path}", "success")
            except Exception as exc:              # disk full, permissions...
                self.toasts.show(f"Export failed: {exc}", "error")

    def quick_save(self) -> None:
        import time
        name, ok = QInputDialog.getText(self, "Save board", "Save the board on the server as:",
                                        text=time.strftime("board-%Y%m%d-%H%M"))
        if ok and name.strip():
            self.controller.save(name.strip())

    def open_saves(self) -> None:
        SavesDialog(self.controller, self).exec()

    def open_room_settings(self) -> None:
        if not _safe(self.controller.is_owner):
            return
        dlg = RoomSettingsDialog(self.controller.room or {}, self)
        if dlg.exec():
            changes = dlg.changes()
            if changes:
                self.controller.set_room(**changes)

    def toggle_theme(self) -> None:
        mode = "light" if theme.manager().dark else "dark"
        theme.apply_theme(None, mode)
        self.settings.setValue("ui/theme", mode)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def focus_chat(self) -> None:
        self.dock.show()
        self.tabs.setCurrentIndex(self.TAB_CHAT)
        self.chat.focus_input()

    def leave(self, confirm: bool = False) -> None:
        if confirm and QMessageBox.question(self, "Leave room", "Leave this room?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._end("leave", "")

    # ------------------------------------------------------------------ tab badges, preview
    def _on_unread(self, n: int) -> None:
        self.tabs.setTabText(self.TAB_CHAT, f"Chat ({n})" if n else "Chat")

    def _on_hands(self, n: int) -> None:
        self.tabs.setTabText(self.TAB_PEOPLE, f"People ✋{n}" if n else "People")

    def _on_preview(self, on: bool, seq: int) -> None:
        if on:
            self.preview_banner.show_message(f"Previewing history at seq {seq} — read-only", "info", "Exit preview")
        else:
            self.preview_banner.hide()

    # ------------------------------------------------------------------ shutdown
    def _save_settings(self) -> None:
        self.settings.setValue("main/geometry", self.saveGeometry())
        self.settings.setValue("main/state", self.saveState())

    def _end(self, reason: str, message: str) -> None:
        """Close the network session and hand control back to the app shell."""
        self._ending = True
        self._save_settings()
        try:
            self.net.close()
        except Exception:
            pass
        self.session_ended.emit(reason, message)

    def closeEvent(self, ev) -> None:  # noqa: N802
        if not self._ending:
            self._end("quit", "")
        else:
            self._save_settings()
        super().closeEvent(ev)
