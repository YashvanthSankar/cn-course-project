"""The LANBoard main window: wires the controller, the canvas and every panel together.

Layout (canvas first: the board fills the window, controls float over it)::

    ┌─ TopBar: logo · room · code chip · sync pill ······ avatars · Invite · actions ─┐
    │ ┌──┐                                                  │ People Chat History Net │
    │ │To│            CanvasView                            │                         │
    │ │ol│     (toasts + banners at the top centre)         │   side panel            │
    │ │s │                                     ┌─minimap─┐  │   (collapsible,         │
    │ └──┘       [ style bar: colours · width ]└─ zoom ──┘  │    Ctrl+B)              │
    └─ NetStrip: ● Connected · host:port · me ·········· RTT · seq · #hash · pending ─┘

The window never touches sockets or the board model directly: it calls the
public ``SessionController`` / ``CanvasView`` methods (see docs/CLIENT_API.md) and
reacts to their signals. When the session must end (Leave, fatal error such as
KICKED, cancelled password prompt) it emits ``session_ended(reason, message)``
and the application shell (``client/__main__.py``) decides whether to show the
connect dialog again or quit.

Notices that need no decision (joins and leaves, role changes, reconnects,
saves, exports, errors) are shown as toasts; only questions use dialogs.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QSettings, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QFrame, QInputDialog, QMainWindow, QMenu, QMessageBox, QSplitter,
                               QVBoxLayout, QWidget)

from . import theme
from .chat import ChatPanel
from .dialogs import RoomSettingsDialog, SavesDialog, ask_password, export_dialog
from .history import HistoryPanel
from .network_panel import NetworkPanel
from .overlays import InvitePopover, NetStrip, ShortcutsOverlay, ZoomPill
from .participants import ParticipantsPanel
from .side_panel import SideTabs
from .toolbar import StyleBar, ToolPalette
from .topbar import TopBar
from .widgets import Banner, FloatingCard, ToastHost

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
STATE_TOKENS = {"joined": "success", "reconnecting": "warn", "connecting": "info",
                "authenticating": "info", "closed": "danger", "idle": "muted"}
ROLE_TEXT = {"owner": "the owner", "editor": "an editor", "viewer": "a viewer (read-only)"}
GRID_STYLES = (("dots", "Dots"), ("lines", "Lines"), ("none", "None"))
SIDE_MIN, SIDE_DEFAULT = 340, 400
EDGE = 16


def _safe(fn, default=False):
    """Call a controller predicate that may fail before JOINED (me/room still None)."""
    try:
        return fn()
    except Exception:
        return default


class MainWindow(QMainWindow):
    """Top-level window for one session (one room on one server)."""

    session_ended = Signal(str, str)

    def __init__(self, controller, canvas=None, settings: QSettings | None = None,
                 lan_addresses: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.net = controller.net
        self.settings = settings or QSettings()
        self.lan_addresses = list(lan_addresses or [])
        self._conn_state = "idle"
        self._joined_once = False
        self._last_sync: dict | None = None
        self._was_reconnecting = False
        self._ending = False
        self._hosting_announced = False
        self._can_undo = self._can_redo = False
        self._known_people: dict[str, bool] | None = None
        self._known_hands: dict[str, bool] = {}
        self._names: dict[str, str] = {}
        self._my_role: str | None = None
        self._pending_save: str | None = None
        self._icon_actions: list[tuple[QAction, str, str | None]] = []
        self.setWindowIcon(theme.app_icon())
        self.setWindowTitle("LANBoard")
        self.setObjectName("MainWindow")
        self.setMinimumSize(1024, 640)

        if canvas is None:
            from lanboard.client.canvas.view import CanvasView
            canvas = CanvasView(controller)
        self.canvas = canvas

        self._build_actions()
        self._build_layout()
        self._build_overlays()
        self._build_topbar()
        self._connect_signals()
        self._restore_settings()

        if getattr(controller, "room", None):
            self.on_room_changed(controller.room)
        if getattr(controller, "me", None):
            self.on_me_changed(controller.me)
        self._on_people()
        self._update_permissions()
        self.retheme()
        theme.on_theme_change(self, self.retheme)

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
            act.triggered.connect(slot)
        else:
            act.triggered.connect(lambda _=False: slot())
        if icon_name:
            self._icon_actions.append((act, icon_name, on_color))
        self.addAction(act)
        return act

    def _build_actions(self) -> None:
        c = self.canvas
        self.act_undo = self._act("Undo", "undo", self.controller.undo, "Ctrl+Z")
        self.act_redo = self._act("Redo", "redo", self.controller.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.act_clear = self._act("Clear board…", "trash", self.confirm_clear,
                                   tip="Clear the board for everyone")
        self.act_lock = self._act("Lock board", "lock", self._on_lock, checkable=True,
                                  tip="Lock the board (owner only): only you can draw", on_color="accent")
        self.act_present = self._act("Present", "present", self._on_present, checkable=True,
                                     tip="Present (owner or chosen presenter): broadcast your view so others "
                                         "can follow it", on_color="accent")
        self.act_hand = self._act("Raise hand", "hand", self._on_hand, checkable=True,
                                  tip="Raise / lower your hand", on_color="warn")
        self.act_image = self._act("Insert image…", "image", self.insert_image)
        self.act_pdf = self._act("Import PDF…", "pdf", self.import_pdf, tip="Import PDF pages as images")
        self.act_export = self._act("Export…", "export", self.export, "Ctrl+E", tip="Export PNG / SVG / PDF")
        self.act_saves = self._act("Saved boards…", "folder", self.open_saves,
                                   tip="Save / load named boards on the server")
        self.act_save = self._act("Save board…", "save", self.quick_save, "Ctrl+S")
        self.act_settings = self._act("Room settings…", "settings", self.open_room_settings,
                                      tip="Room settings (owner): name, password, permissions")
        self.act_theme = self._act("Toggle dark mode", "moon", self.toggle_theme)
        self.act_leave = self._act("Leave room", "leave", self.leave)
        self.act_panel = self._act("Side panel", "panel", self.toggle_side_panel, "Ctrl+B", checkable=True,
                                   tip="Show / hide the side panel", on_color="accent")
        self.act_shortcuts = self._act("Keyboard shortcuts", "keyboard", self.show_shortcuts, ["?", "F1"])
        self.act_minimap = self._act("Show minimap", "map", self.set_minimap_visible, "Ctrl+Shift+M",
                                     checkable=True)
        self.act_fullscreen = self._act("Full screen", None, self.toggle_fullscreen, "F11")
        self.act_fit = self._act("Fit board", "fit", lambda: c.zoom_fit(), "Ctrl+1")
        self.act_zoom_reset = self._act("Reset zoom", None, lambda: c.zoom_reset(), "Ctrl+0")
        self._act("Delete selection", None, lambda: c.delete_selection(), ["Delete", "Backspace"])
        self._act("Select all", None, lambda: c.select_all(), "Ctrl+A")
        self._act("Focus chat", None, self.focus_chat, ["Ctrl+Return", "Ctrl+Enter"])
        self.grid_group = QActionGroup(self)
        self.grid_actions: dict[str, QAction] = {}
        for key, text in GRID_STYLES:
            a = QAction(text, self, checkable=True)
            a.triggered.connect(lambda _=False, k=key: self.set_grid(k))
            self.grid_group.addAction(a)
            self.grid_actions[key] = a

    def _build_layout(self) -> None:
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.topbar = TopBar(self)
        v.addWidget(self.topbar)

        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setObjectName("BodySplit")
        self.body.setHandleWidth(1)
        self.body.setChildrenCollapsible(False)
        self.stage = QWidget()
        self.stage.setObjectName("Stage")
        self.stage.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sl = QVBoxLayout(self.stage)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.canvas)
        self.body.addWidget(self.stage)

        self.side = QFrame()
        self.side.setObjectName("SidePanel")
        self.side.setMinimumWidth(SIDE_MIN)
        side_lay = QVBoxLayout(self.side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = SideTabs()
        side_lay.addWidget(self.tabs)
        self.participants = ParticipantsPanel(self.controller, self.canvas)
        self.chat = ChatPanel(self.controller)
        self.history = HistoryPanel(self.controller, self.canvas)
        self.network = NetworkPanel(self.net, self.controller)
        self.TAB_PEOPLE = self.tabs.addTab(self.participants, "People", "users")
        self.TAB_CHAT = self.tabs.addTab(self.chat, "Chat", "chat")
        self.TAB_HISTORY = self.tabs.addTab(self.history, "History", "history")
        self.TAB_NET = self.tabs.addTab(self.network, "Network", "network")
        self.body.addWidget(self.side)
        self.body.setStretchFactor(0, 1)
        self.body.setStretchFactor(1, 0)
        v.addWidget(self.body, 1)

        self.strip = NetStrip(self)
        v.addWidget(self.strip)
        self.status_labels = self.strip.labels
        self.hover_label = self.strip.hover_label
        self.setCentralWidget(central)
        self._side_anim: QVariantAnimation | None = None
        self._side_width = SIDE_DEFAULT

        self._set_status_state("idle")
        self.status_labels["rtt"].setText("RTT –")
        self.status_labels["seq"].setText("seq 0")
        self.status_labels["pending"].setText("0 pending")
        self.status_labels["zoom"].setText("100%")

    def _build_overlays(self) -> None:
        st = self.stage
        self.rail = ToolPalette(self.canvas, st)
        self.style_bar = StyleBar(self.canvas, st)
        self.style_bar.layout_changed.connect(self._place_overlays)
        self.minimap = None
        self.zoom_controls = None
        try:
            from lanboard.client.canvas.minimap import Minimap, ZoomControls
            from lanboard.client.canvas.view import CanvasView
            if not isinstance(self.canvas, CanvasView):
                raise TypeError("not a real CanvasView")
            self.minimap = Minimap(self.canvas, st)
            self.zoom_controls = ZoomControls(self.canvas, st)
        except Exception:
            self.minimap = None
            self.zoom_controls = ZoomPill(self.canvas, st)
        self.banner = Banner(st)
        self._banner_action = lambda: None
        self.banner.action.connect(lambda: self._banner_action())
        self.preview_banner = Banner(st, offset=62)
        self.toasts = ToastHost(st, top_offset=self._toast_top)
        self.invite = InvitePopover(self)
        self.invite.copied.connect(lambda *_: self.toasts.show("Invite copied to the clipboard", "success"))
        self.shortcuts = ShortcutsOverlay(self)
        st.installEventFilter(self)
        for w in (self.rail, self.style_bar, self.zoom_controls, self.minimap):
            if w is not None:
                w.show()
                w.raise_()

    def _build_topbar(self) -> None:
        tb = self.topbar
        for act in (self.act_undo, self.act_redo):
            tb.add_action(act)
        tb.add_separator()
        tb.add_action(self.act_present, text_beside=True)
        hand_btn = tb.add_action(self.act_hand)
        hand_btn.setProperty("warnOn", True)
        tb.add_action(self.act_lock)
        tb.add_separator()
        self.insert_menu = QMenu(self)
        self.insert_menu.addAction(self.act_image)
        self.insert_menu.addAction(self.act_pdf)
        tb.add_menu_button("insert", "Insert an image or PDF pages", self.insert_menu)
        tb.add_action(self.act_export)
        self.more_menu = self._build_more_menu()
        self.more_btn = tb.add_menu_button("more", "More: saves, room settings, view options", self.more_menu)
        tb.add_separator()
        tb.add_action(self.act_theme)
        tb.add_action(self.act_panel)
        tb.add_action(self.act_leave)
        tb.invite_btn.clicked.connect(self.open_invite)
        tb.avatars.clicked.connect(lambda: self.show_tab(self.TAB_PEOPLE))
        tb.copied.connect(lambda *_: self.toasts.show("Room code and address copied", "success"))

    def _build_more_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction(self.act_saves)
        m.addAction(self.act_save)
        m.addAction(self.act_export)
        m.addSeparator()
        m.addAction(self.act_clear)
        m.addAction(self.act_settings)
        m.addSeparator()
        view = m.addMenu("View")
        self.view_menu = view
        grid = view.addMenu("Grid")
        for a in self.grid_actions.values():
            grid.addAction(a)
        view.addAction(self.act_minimap)
        view.addSeparator()
        view.addAction(self.act_fit)
        view.addAction(self.act_zoom_reset)
        view.addAction(self.act_fullscreen)
        m.addAction(self.act_shortcuts)
        m.addAction(self.act_theme)
        return m

    def _connect_signals(self) -> None:
        ctl, net, c = self.controller, self.net, self.canvas
        ctl.room_changed.connect(self.on_room_changed)
        ctl.me_changed.connect(self.on_me_changed)
        ctl.sync_status.connect(self.on_sync_status)
        ctl.undo_state.connect(self.on_undo_state)
        ctl.notice.connect(self.on_notice)
        ctl.saves_ready.connect(self._on_saves)
        ctl.connection_state.connect(self.on_connection_state)
        ctl.signal_received.connect(self.on_signal)
        ctl.participants_changed.connect(self._update_permissions)
        ctl.participants_changed.connect(self._on_people)
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
        if hasattr(c, "follow_changed"):
            c.follow_changed.connect(self.on_follow_changed)
        if hasattr(c, "tool_changed"):
            c.tool_changed.connect(lambda *_: self._update_style_context())
        scene = getattr(c, "canvas", None)
        if scene is not None and hasattr(scene, "selection_changed"):
            scene.selection_changed.connect(self._update_style_context)
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
        try:
            self._side_width = max(SIDE_MIN, int(self.settings.value("ui/side_width", SIDE_DEFAULT)))
        except (TypeError, ValueError):
            self._side_width = SIDE_DEFAULT
        open_ = str(self.settings.value("ui/side_open", "true")).lower() != "false"
        self.body.setSizes([max(200, self.width() - self._side_width), self._side_width])
        self.act_panel.setChecked(open_)
        if not open_:
            self.side.hide()
        grid = str(self.settings.value("ui/grid", "dots"))
        self.set_grid(grid if grid in self.grid_actions else "dots")
        mini = str(self.settings.value("ui/minimap", "true")).lower() != "false"
        self.act_minimap.setChecked(mini)
        self.set_minimap_visible(mini)

    def retheme(self) -> None:
        t = theme.tokens()
        dark = theme.manager().dark
        for act, name, on in self._icon_actions:
            if act is self.act_theme:
                name = "sun" if dark else "moon"
            act.setIcon(theme.icon(name, t["text2"], 20, on_color=t[on] if on else None))
        self.act_theme.setText("Light mode" if dark else "Dark mode")
        self._set_status_state(self._conn_state)
        fn = getattr(self.canvas, "set_board_theme", None)
        if callable(fn):
            fn(dark)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.stage and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place_overlays()
        return False

    def _toast_top(self) -> int:
        y = EDGE
        for b in (self.banner, self.preview_banner):
            if b.isVisible():
                y = max(y, b.bottom() + 8)
        return y

    def _place_overlays(self) -> None:
        """Pin the floating controls to the stage edges (called on every stage resize).

        The style bar is centred at the bottom. When the stage is too narrow for
        it to sit between the tool palette and the zoom controls, the bar first
        switches to its compact form, and the zoom controls and minimap move up
        above it instead of overlapping.
        """
        w, h = self.stage.width(), self.stage.height()
        s = FloatingCard.SHADOW
        self.rail.fit_height(h - 2 * EDGE)
        self.rail.adjustSize()
        self.rail.move(EDGE - s, max(EDGE - s, (h - self.rail.height()) // 2))
        zc, mm, bar = self.zoom_controls, self.minimap, self.style_bar
        zw = zh = 0
        zpad = (s, s + 2) if isinstance(zc, FloatingCard) else (0, 0)
        if zc is not None:
            zc.adjustSize()
            zw, zh = zc.width() - 2 * zpad[0], zc.height() - 2 * zpad[1]
        left_edge = self.rail.x() + self.rail.width() - s + 8
        bar.set_compact(False)
        bar.adjustSize()
        if bar.width() - 2 * s > w - left_edge - EDGE:
            bar.set_compact(True)
            bar.adjustSize()
        bw = bar.width() - 2 * s
        x = (w - bw) // 2
        x = min(x, w - EDGE - zw - 8 - bw)
        x = max(x, left_edge)
        bar_top = h - EDGE - (bar.height() - 2 * s - 4)
        bar.move(x - s, bar_top - s + 2)
        bar_visible = bar.isVisible() and not bar.isHidden()
        collide = bar_visible and x + bw > w - EDGE - zw - 8
        zy = h - EDGE - zh if not collide else bar_top - 10 - zh
        if zc is not None:
            zc.move(w - EDGE - zw - zpad[0], zy - zpad[1])
        if mm is not None:
            my = zy - 10 - mm.height()
            if not collide and bar_visible and x + bw + 8 > w - EDGE - mm.width():
                my = min(my, bar_top - 10 - mm.height())
            mm.move(w - EDGE - mm.width(), my)
        self._update_safe_margins(bar_visible, bar_top)
        self.toasts.relayout()

    def _update_safe_margins(self, bar_visible: bool, bar_top: int) -> None:
        """Tell the canvas which edges the overlays cover, so Fit frames content clear of them.

        The palette insets the left edge and the style bar the bottom. The zoom
        controls + minimap sit in the bottom-right corner: that block insets
        whichever edge (right or bottom) costs the smaller share of the view.
        """
        fn = getattr(self.canvas, "set_safe_margins", None)
        if fn is None:
            return
        w, h = self.stage.width(), self.stage.height()
        s, gap = FloatingCard.SHADOW, 8
        left = self.rail.x() + self.rail.width() - s + gap if self.rail.isVisible() else 0
        bottom = h - bar_top + gap if bar_visible else 0
        right = 0
        corner = [c for c in (self.zoom_controls, self.minimap) if c is not None and c.isVisible()]
        if corner and w > 0 and h > 0:
            cx = min(c.x() for c in corner)
            cy = min(c.y() for c in corner)
            cw, ch = w - cx + gap, h - cy + gap
            if cw / w < max(0, ch - bottom) / h:
                right = cw
            else:
                bottom = max(bottom, ch)
        fn(left, 0, right, bottom)

    def _update_style_context(self, *_args) -> None:
        tool = self.rail.current_tool()
        types: set[str] = set()
        if tool == "select":
            scene = getattr(self.canvas, "canvas", None)
            try:
                types = {it.obj.get("type") for it in scene.selected_items()} if scene is not None else set()
            except Exception:
                types = set()
        visible = self.style_bar.set_context(tool, types) and bool(_safe(self.controller.can_edit))
        self.style_bar.setVisible(visible)
        if visible:
            self.style_bar.raise_()
        self._place_overlays()

    def set_minimap_visible(self, on: bool) -> None:
        on = bool(on)
        if self.minimap is not None:
            self.minimap.setVisible(on)
            self._place_overlays()
        if self.act_minimap.isChecked() != on:
            self.act_minimap.setChecked(on)
        self.settings.setValue("ui/minimap", "true" if on else "false")

    def set_grid(self, style: str) -> None:
        if style in self.grid_actions:
            self.grid_actions[style].setChecked(True)
        fn = getattr(self.canvas, "set_grid", None)
        if callable(fn):
            fn(style)
        self.settings.setValue("ui/grid", style)

    def open_invite(self) -> None:
        if self.invite.isVisible():
            self.invite.close_popover()
            return
        room = self.controller.room or {}
        self.invite.set_info(getattr(self.net, "host", ""), getattr(self.net, "port", 0),
                             room.get("code", ""), self.lan_addresses)
        self.invite.open_below(self.topbar.invite_btn)

    def show_shortcuts(self) -> None:
        self.shortcuts.open()

    def show_tab(self, idx: int) -> None:
        if not self.side.isVisible():
            self.toggle_side_panel(True)
        self.tabs.setCurrentIndex(idx)

    def side_panel_open(self) -> bool:
        return self.act_panel.isChecked()

    def toggle_side_panel(self, checked: bool | None = None) -> None:
        """Slide the side panel in or out (Ctrl+B)."""
        open_ = (not self.side.isVisible()) if checked is None else bool(checked)
        self.act_panel.setChecked(open_)
        self.settings.setValue("ui/side_open", "true" if open_ else "false")
        if self._side_anim is not None:
            self._side_anim.stop()
        total = sum(self.body.sizes()) or self.body.width()
        if open_ and not self.side.isVisible():
            self.side.setMinimumWidth(0)
            self.side.show()
            self.body.setSizes([total, 0])
            start, end = 0, self._side_width
        elif not open_ and self.side.isVisible():
            self._side_width = max(SIDE_MIN, self.side.width())
            self.settings.setValue("ui/side_width", self._side_width)
            self.side.setMinimumWidth(0)
            start, end = self.side.width(), 0
        else:
            return
        anim = QVariantAnimation(self, startValue=start, endValue=end, duration=200)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda v: self.body.setSizes([total - int(v), int(v)]))

        def done() -> None:
            if open_:
                self.side.setMinimumWidth(SIDE_MIN)
                self.body.setSizes([total - self._side_width, self._side_width])
            else:
                self.side.hide()
        anim.finished.connect(done)
        self._side_anim = anim
        anim.start()

    def on_room_changed(self, room: dict) -> None:
        room = room or {}
        name, code = room.get("name") or room.get("code", ""), room.get("code", "")
        self.setWindowTitle(f"LANBoard — {name} ({code})" if code else "LANBoard")
        self.topbar.set_room(room, getattr(self.net, "host", ""), getattr(self.net, "port", 0), self.lan_addresses)
        was_locked = bool(self.act_lock.isChecked())
        self.act_lock.blockSignals(True)
        self.act_lock.setChecked(bool(room.get("locked")))
        self.act_lock.setText("Unlock board" if room.get("locked") else "Lock board")
        self.act_lock.blockSignals(False)
        if bool(room.get("locked")) != was_locked and code and not _safe(self.controller.is_owner):
            self.toasts.show("The owner locked the board" if room.get("locked") else "The board is unlocked",
                             "warn" if room.get("locked") else "info")
        presenter = room.get("presenter")
        me_id = (self.controller.me or {}).get("user_id")
        if presenter and presenter == me_id and presenter != getattr(self, "_last_presenter", None) \
                and not _safe(self.controller.is_owner):
            self.toasts.show("The owner made you the presenter. Press Present to share your view.", "info")
        if presenter and presenter != me_id and presenter != getattr(self, "_last_presenter", None):
            who = _safe(lambda: self.controller.name_of(presenter), presenter)
            self.toasts.show(f"{who} is presenting. Right-click them in People and choose Follow.", "info")
        self._last_presenter = presenter
        self._update_permissions()

    def on_me_changed(self, me: dict) -> None:
        me = me or {}
        role = me.get("role")
        self.status_labels["me"].setText(f"{me.get('username', '?')} · {role or '?'}")
        if self._my_role is not None and role and role != self._my_role:
            self.toasts.show(f"You are now {ROLE_TEXT.get(role, role)}", "info")
        self._my_role = role or self._my_role
        self.act_hand.blockSignals(True)
        self.act_hand.setChecked(bool(me.get("hand")))
        self.act_hand.setText("Lower hand" if me.get("hand") else "Raise hand")
        self.act_hand.blockSignals(False)
        self._update_permissions()

    def _on_people(self) -> None:
        """Refresh the avatar stack and toast joins / leaves (not the initial roster)."""
        parts = dict(getattr(self.controller, "participants", None) or {})
        me_id = (self.controller.me or {}).get("user_id")
        people = sorted(parts.values(), key=lambda p: (p.get("user_id") != me_id, (p.get("username") or "").lower()))
        self.topbar.set_people(people)
        now = {uid: bool(p.get("online", True)) for uid, p in parts.items()}
        if self._known_people is not None and self._conn_state == "joined":
            for uid, online in now.items():
                if uid == me_id:
                    continue
                was = self._known_people.get(uid)
                name = parts[uid].get("username") or uid
                if online and not was:
                    self.toasts.show_grouped("joined" if was is None else "back", name,
                                             "joined" if was is None else "reconnected", "success")
                elif was and not online:
                    self.toasts.show_grouped("lost", name, "lost connection", "warn")
            for uid in set(self._known_people) - set(now):
                if uid != me_id and self._known_people[uid]:
                    self.toasts.show_grouped("left", self._names.get(uid, "Someone"), "left the room", "info")
            for uid, p in parts.items():
                if uid != me_id and p.get("hand") and not self._known_hands.get(uid, False):
                    self.toasts.show(f"✋ {p.get('username') or uid} raised a hand", "warn")
        self._names.update({uid: p.get("username") or uid for uid, p in parts.items()})
        if parts:
            self._known_people = now
            self._known_hands = {uid: bool(p.get("hand")) for uid, p in parts.items()}

    def on_sync_status(self, st: dict) -> None:
        self._last_sync = dict(st)
        self._render_sync()
        self.status_labels["seq"].setText(f"seq {st.get('seq', 0)}")
        h = st.get("hash")
        self.status_labels["hash"].setText(f"#{str(h)[:8]}" if h else "")
        self.status_labels["pending"].setText(f"{st.get('pending', 0)} pending")

    def _render_sync(self) -> None:
        """The badge text depends on the connection state too (connecting vs offline)."""
        st = dict(getattr(self, "_last_sync", None) or {"state": "offline", "seq": 0, "pending": 0})
        st["conn"] = self._conn_state
        st["joined_once"] = self._joined_once
        self.topbar.set_sync(st)

    def on_undo_state(self, can_undo: bool, can_redo: bool) -> None:
        self._can_undo, self._can_redo = bool(can_undo), bool(can_redo)
        self._update_permissions()

    def on_notice(self, level: str, text: str) -> None:
        self.toasts.show(text, level)

    def on_signal(self, user_id: str, kind: str) -> None:
        """Relayed SIGNALs (pings are drawn by the canvas). Raised hands arrive as
        participant updates instead, see :meth:`_on_people`."""

    def on_follow_changed(self, user_id) -> None:
        """Canvas stopped (or started) following, e.g. because the user panned away."""
        uid = user_id or None
        if self.participants.following != uid:
            was = self.participants.following
            self.participants.following = uid
            self.participants.refresh()
            if was and uid is None:
                self.toasts.show("Stopped following (you moved the view)", "info")

    def on_connection_state(self, state: str) -> None:
        prev, self._conn_state = self._conn_state, state
        self._joined_once = self._joined_once or state == "joined"
        self._set_status_state(state)
        self._render_sync()
        host = f"{getattr(self.net, 'host', '')}:{getattr(self.net, 'port', '')}"
        if state in ("connecting", "authenticating"):
            if prev == "reconnecting":
                return
            self._show_banner(f"Connecting to {host}…", "info", "Cancel", self.leave)
        elif state == "reconnecting":
            self._was_reconnecting = True
            self._show_banner("Connection lost — reconnecting… your edits are queued", "warn",
                              "Leave", self.leave)
        elif state == "joined":
            self.banner.hide()
            self.toasts.relayout()
            if self._was_reconnecting:
                self._was_reconnecting = False
                self.toasts.show("Reconnected. Missed changes were replayed.", "success")
        elif state == "closed" and not self._ending:
            self._show_banner("Disconnected from the server", "error", "Back to start",
                              lambda: self._end("leave", ""))

    def _show_banner(self, text: str, kind: str, action_text: str, action) -> None:
        self._banner_action = action
        self.banner.show_message(text, kind, action_text)
        self.toasts.relayout()

    def _set_status_state(self, state: str) -> None:
        t = theme.tokens()
        col = t.get(STATE_TOKENS.get(state, "muted"), t["muted"])
        self.status_labels["state"].setText(f"<span style='color:{col}; font-size:13px'>●</span>&nbsp; "
                                            f"<b>{STATE_LABELS.get(state, state)}</b>")
        host, port = getattr(self.net, "host", ""), getattr(self.net, "port", "")
        if self.lan_addresses:
            self.status_labels["server"].setText(f"hosting · {self.lan_addresses[0]}:{port}")
        else:
            self.status_labels["server"].setText(f"TCP {host}:{port}" if host else "")

    def on_rtt(self, ms: float) -> None:
        t = theme.tokens()
        tok = "success" if ms < 40 else ("warn" if ms < 200 else "danger")
        self.status_labels["rtt"].setText(f"RTT <span style='color:{t[tok]}'>{ms:.0f} ms</span>")

    def on_zoom(self, z: float) -> None:
        pct = z * 100 if z <= 50 else z
        self.status_labels["zoom"].setText(f"{pct:.0f}%")

    def on_joined(self, payload: dict) -> None:
        if self.lan_addresses and not self._hosting_announced:
            self._hosting_announced = True
            code = (payload.get("room") or {}).get("code", "")
            port = getattr(self.net, "port", "")
            self.toasts.show(f"You're hosting. Others join at {self.lan_addresses[0]}:{port}, room {code}",
                             "success", 9000)

    def on_net_error(self, code: str, msg: str) -> None:
        QTimer.singleShot(0, lambda: self._handle_error(code, msg))

    def _handle_error(self, code: str, msg: str) -> None:
        if self._ending:
            return
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
        role = (getattr(ctl, "me", None) or {}).get("role")
        self.act_save.setEnabled(role in ("owner", "editor"))
        me_id = (getattr(ctl, "me", None) or {}).get("user_id")
        may_present = owner or bool(me_id and (getattr(ctl, "room", None) or {}).get("presenter") == me_id)
        self.act_present.setEnabled(may_present)
        if not may_present and self.act_present.isChecked():
            self.act_present.setChecked(False)
            self._on_present(False)
        self._update_style_context()

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
        self.act_present.setText("Presenting" if checked else "Present")
        if checked:
            self.toasts.show("You are presenting. Others can follow your view from People.", "info")

    def _on_hand(self, checked: bool) -> None:
        self.controller.send_signal("hand_up" if checked else "hand_down")
        self.act_hand.setText("Lower hand" if checked else "Raise hand")
        if checked:
            self.toasts.show("Hand raised. The owner sees ✋ next to your name.", "warn")

    def insert_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert image", "",
                                              "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if path:
            self.canvas.insert_image_file(path)

    def import_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF documents (*.pdf)")
        if path:
            self.canvas.import_pdf(path)

    def export(self) -> None:
        code = (self.controller.room or {}).get("code", "board")
        path = export_dialog(self, f"lanboard-{code}")
        if path:
            try:
                self.canvas.export(path)
            except Exception as exc:
                self.toasts.show(f"Export failed: {exc}", "error")

    def quick_save(self) -> None:
        import time
        name, ok = QInputDialog.getText(self, "Save board", "Save the board on the server as:",
                                        text=time.strftime("board-%Y%m%d-%H%M"))
        if ok and name.strip():
            self._pending_save = name.strip()[:64]
            self.controller.save(name.strip())

    def _on_saves(self, saves: list) -> None:
        """The server answers SAVE with the new list: confirm a Ctrl+S save with a toast."""
        name = self._pending_save
        if name and any(s.get("name") == name for s in saves or []):
            self._pending_save = None
            self.toasts.show(f"Saved “{name}” on the server", "success")

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
                self.toasts.show("Room settings updated", "success")

    def toggle_theme(self) -> None:
        mode = "light" if theme.manager().dark else "dark"
        theme.apply_theme(None, mode)
        self.settings.setValue("ui/theme", mode)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def focus_chat(self) -> None:
        self.show_tab(self.TAB_CHAT)
        self.chat.focus_input()

    def leave(self, confirm: bool = False) -> None:
        if confirm and QMessageBox.question(self, "Leave room", "Leave this room?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._end("leave", "")

    def _on_unread(self, n: int) -> None:
        self.tabs.set_badge(self.TAB_CHAT, n, "accent")

    def _on_hands(self, n: int) -> None:
        self.tabs.set_badge(self.TAB_PEOPLE, n, "warn")

    def _on_preview(self, on: bool, seq: int) -> None:
        if on:
            self.preview_banner.show_message(f"Time-lapse at seq {seq}. The live board keeps syncing underneath.",
                                             "info", "Back to live")
        else:
            self.preview_banner.hide()
        self.toasts.relayout()

    def _save_settings(self) -> None:
        self.settings.setValue("main/geometry", self.saveGeometry())
        if self.side.isVisible() and self.side.width() >= SIDE_MIN:
            self.settings.setValue("ui/side_width", self.side.width())

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
