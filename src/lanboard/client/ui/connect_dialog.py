"""Welcome / connect screen.

Left: a painted hero panel with the app name and tagline. Right: the form.

* **Username**, remembered with QSettings.
* **Join room / Host new room** segmented toggle.
* **Nearby servers**: live list from ``DiscoveryBrowser.servers_changed`` (UDP
  broadcast discovery). Each server shows its name and ``host:port`` with its rooms
  underneath (lock icon when password protected). Clicking a room fills the fields;
  double-clicking joins straight away.
* Manual **host** and **port** (default ``config.TCP_PORT``).
* Host mode: room **code** (with a generator for codes like ``K7Q-2MX``), room name,
  optional password and **Start a server on this computer**. That option launches
  ``lanboard.server`` as a child process (see :mod:`.server_launcher`), waits until
  its port accepts, then connects to 127.0.0.1. The LAN IPv4 addresses that other
  people should type are shown under the option.

The dialog validates inline and, when accepted, :meth:`ConnectDialog.params`
returns a :class:`ConnectParams`.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from lanboard.common import config as C

from . import theme
from .server_launcher import ServerProcess, lan_ipv4_addresses
from .widgets import PortEdit, Segmented

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I lookalikes
ROOM_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,%d}$" % C.MAX_ROOM_CODE)
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:_\[\]%]+$")


def generate_room_code(rng: random.Random | None = None) -> str:
    """A short, easy-to-read code such as ``K7Q-2MX``."""
    rng = rng or random.SystemRandom()
    pick = lambda n: "".join(rng.choice(CODE_ALPHABET) for _ in range(n))  # noqa: E731
    return f"{pick(3)}-{pick(3)}"


@dataclass
class ConnectParams:
    """What the user chose; maps 1:1 onto ``NetClient.connect_to``."""

    host: str = "127.0.0.1"
    port: int = C.TCP_PORT
    username: str = ""
    room: str = ""
    create: bool = False
    password: str | None = None
    room_name: str | None = None
    start_server: bool = False
    server: object | None = field(default=None, repr=False)   # ServerProcess when we started one
    lan_addresses: list[str] = field(default_factory=list)


class HeroPanel(QWidget):
    """The painted left half of the welcome screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor("#5b6ef0"))
        g.setColorAt(0.55, QColor("#4352d6"))
        g.setColorAt(1.0, QColor("#2c3699"))
        p.fillRect(r, g)
        # faint whiteboard doodles to suggest a shared canvas
        faint = QColor(255, 255, 255, 34)
        pen = QPen(faint, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        w, h = r.width(), r.height()
        p.drawEllipse(QPointF(w * 0.78, h * 0.62), 54, 54)
        p.drawRoundedRect(QRectF(w * 0.12, h * 0.70, 120, 76), 10, 10)
        squig = QPainterPath(QPointF(w * 0.10, h * 0.93))
        squig.cubicTo(w * 0.35, h * 0.80, w * 0.45, h * 1.02, w * 0.70, h * 0.88)
        squig.cubicTo(w * 0.80, h * 0.83, w * 0.88, h * 0.86, w * 0.95, h * 0.80)
        p.drawPath(squig)
        p.drawLine(QPointF(w * 0.55, h * 0.60), QPointF(w * 0.66, h * 0.50))
        p.drawLine(QPointF(w * 0.66, h * 0.50), QPointF(w * 0.62, h * 0.505))
        # two collaborator cursors with name tags
        for (cx, cy), name, col in (((w * 0.70, h * 0.73), "Asha", "#ffd166"), ((w * 0.33, h * 0.62), "Ravi", "#06d6a0")):
            arrow = QPainterPath(QPointF(cx, cy))
            arrow.lineTo(cx, cy + 18)
            arrow.lineTo(cx + 5, cy + 13.5)
            arrow.lineTo(cx + 12.5, cy + 13)
            arrow.closeSubpath()
            p.setPen(QPen(QColor("white"), 1.2))
            p.setBrush(QColor(col))
            p.drawPath(arrow)
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(name) + 12
            tag = QRectF(cx + 12, cy + 16, tw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(tag, 9, 9)
            p.setPen(QColor("#1b2130"))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, name)
        # logo + text
        theme.paint_logo(p, QRectF(36, 40, 52, 52))
        p.setPen(QColor("white"))
        f = QFont(self.font())
        f.setPixelSize(30)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRectF(36, 108, w - 60, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "LANBoard")
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(QRectF(36, 150, w - 66, 60), Qt.TextFlag.TextWordWrap,
                   "Draw together in real time on your local network. No internet, no accounts.")
        bullets = ["Finds servers on your LAN automatically",
                   "Shows every frame in the Network Inspector",
                   "Survives delay, loss and a cut cable"]
        y = 222.0
        f.setPixelSize(13)
        p.setFont(f)
        for b in bullets:
            theme.icon("check", "#b9f6d6", 16).paint(p, QRectF(36, y + 1, 16, 16).toRect())
            p.setPen(QColor(255, 255, 255, 230))
            p.drawText(QRectF(60, y, w - 90, 20), Qt.AlignmentFlag.AlignVCenter, b)
            y += 28
        p.end()


class ConnectDialog(QDialog):
    """The first screen. ``exec()`` then ``params()``."""

    def __init__(self, parent: QWidget | None = None, discovery=None, prefill: ConnectParams | None = None,
                 error_text: str = "", settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LANBoard — connect")
        self.setWindowIcon(theme.app_icon())
        self.setMinimumSize(QSize(920, 740))
        self.settings = settings or QSettings()
        self.servers: list[dict] = []
        self.discovery = None
        self.server_process: ServerProcess | None = None
        self._result: ConnectParams | None = None
        self.lan_addresses = lan_ipv4_addresses()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.hero = HeroPanel()
        root.addWidget(self.hero, 5)

        right = QWidget()
        right.setObjectName("SidePanel")
        right.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.addWidget(right, 7)
        lay = QVBoxLayout(right)
        lay.setContentsMargins(34, 30, 34, 24)
        lay.setSpacing(10)

        self.heading = QLabel("Join a whiteboard")
        self.heading.setProperty("role", "h1")
        lay.addWidget(self.heading)

        # username + mode
        top = QGridLayout()
        top.setHorizontalSpacing(12)
        top.setVerticalSpacing(4)
        top.addWidget(self._caption("Your name"), 0, 0)
        top.addWidget(self._caption("Mode"), 0, 1)
        self.username = QLineEdit()
        self.username.setPlaceholderText("How others will see you")
        self.username.setMaxLength(C.MAX_USERNAME)
        top.addWidget(self.username, 1, 0)
        self.mode = Segmented(["Join room", "Host new room"])
        self.mode.changed.connect(self._on_mode)
        top.addWidget(self.mode, 1, 1)
        top.setColumnStretch(0, 1)
        top.setColumnStretch(1, 1)
        lay.addLayout(top)

        # nearby servers
        lay.addSpacing(6)
        nh = QHBoxLayout()
        nh.addWidget(self._caption("Nearby servers"))
        self.scan_label = QLabel("scanning…")
        self.scan_label.setProperty("role", "muted")
        nh.addWidget(self.scan_label)
        nh.addStretch(1)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setIcon(theme.icon("refresh", theme.tokens()["text2"], 16))
        self.refresh_btn.setToolTip("Probe the network again")
        self.refresh_btn.clicked.connect(self._probe)
        nh.addWidget(self.refresh_btn)
        lay.addLayout(nh)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(18)
        self.tree.setMinimumHeight(120)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.itemClicked.connect(self._on_tree_click)
        self.tree.itemDoubleClicked.connect(self._on_tree_double)
        lay.addWidget(self.tree, 1)

        # host / port
        hp = QGridLayout()
        hp.setHorizontalSpacing(12)
        hp.setVerticalSpacing(4)
        hp.addWidget(self._caption("Server address"), 0, 0)
        hp.addWidget(self._caption("Port"), 0, 1)
        self.host = QLineEdit("127.0.0.1")
        self.host.setPlaceholderText("e.g. 192.168.1.20")
        hp.addWidget(self.host, 1, 0)
        self.port = PortEdit(C.TCP_PORT)
        self.port.setFixedWidth(96)
        self.port.valueChanged.connect(lambda *_: self._update_share())
        hp.addWidget(self.port, 1, 1)
        hp.setColumnStretch(0, 1)
        lay.addLayout(hp)

        # room fields
        rf = QGridLayout()
        rf.setHorizontalSpacing(12)
        rf.setVerticalSpacing(4)
        rf.addWidget(self._caption("Room code"), 0, 0)
        self.name_caption = self._caption("Room name")
        rf.addWidget(self.name_caption, 0, 1)
        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self.room = QLineEdit()
        self.room.setPlaceholderText("e.g. K7Q-2MX")
        self.room.setMaxLength(C.MAX_ROOM_CODE)
        self.room.textChanged.connect(lambda *_: self._update_share())
        code_row.addWidget(self.room, 1)
        self.gen_btn = QToolButton()
        self.gen_btn.setIcon(theme.icon("smart", theme.tokens()["accent"], 16))
        self.gen_btn.setToolTip("Generate a fresh room code")
        self.gen_btn.clicked.connect(lambda: self.room.setText(generate_room_code()))
        code_row.addWidget(self.gen_btn)
        rf.addLayout(code_row, 1, 0)
        self.room_name = QLineEdit()
        self.room_name.setPlaceholderText("e.g. CN Lab — Group 4")
        self.room_name.setMaxLength(64)
        rf.addWidget(self.room_name, 1, 1)
        rf.addWidget(self._caption("Password"), 2, 0, 1, 2)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        rf.addWidget(self.password, 3, 0, 1, 2)
        rf.setColumnStretch(0, 1)
        rf.setColumnStretch(1, 1)
        lay.addLayout(rf)

        # host-mode: start a local server
        self.start_server = QCheckBox("Start a server on this computer")
        self.start_server.toggled.connect(self._on_start_server)
        lay.addWidget(self.start_server)
        self.share = QLabel()
        self.share.setProperty("role", "card")
        self.share.setWordWrap(True)
        self.share.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.share)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)

        btns = QHBoxLayout()
        self.theme_btn = QToolButton()
        self.theme_btn.setToolTip("Toggle light / dark theme")
        self.theme_btn.clicked.connect(self._toggle_theme)
        btns.addWidget(self.theme_btn)
        btns.addStretch(1)
        self.cancel_btn = QPushButton("Quit")
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        self.ok_btn = QPushButton("Join room")
        self.ok_btn.setProperty("role", "primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.setMinimumWidth(150)
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.clicked.connect(self._on_ok)
        btns.addWidget(self.ok_btn)
        lay.addLayout(btns)

        self._load_settings(prefill)
        self._on_mode(self.mode.index())
        self.error.setText(error_text)
        self._retheme_icons()
        theme.manager().changed.connect(lambda *_: self._retheme_icons())

        # discovery
        self.discovery = discovery
        if self.discovery is None:  # real LAN browser unless a test injected a fake
            try:
                from lanboard.client.discovery import DiscoveryBrowser
                self.discovery = DiscoveryBrowser(self)
            except Exception:                     # module missing / socket error
                self.discovery = None
        if self.discovery is not None:
            self.discovery.servers_changed.connect(self.set_servers)
            try:
                self.discovery.start()
            except Exception:
                pass
        self.set_servers([])
        self.finished.connect(self._stop_discovery)

    # -- small helpers ------------------------------------------------------------------
    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text.upper())
        lab.setProperty("role", "section")
        return lab

    def _retheme_icons(self) -> None:
        t = theme.tokens()
        self.theme_btn.setIcon(theme.icon("sun" if theme.manager().dark else "moon", t["text2"], 18))
        self.refresh_btn.setIcon(theme.icon("refresh", t["text2"], 16))
        self.set_servers(self.servers)

    def _toggle_theme(self) -> None:
        mode = "light" if theme.manager().dark else "dark"
        theme.apply_theme(None, mode)
        self.settings.setValue("ui/theme", mode)

    def _stop_discovery(self, *_):
        if self.discovery is not None:
            try:
                self.discovery.stop()
            except Exception:
                pass

    def _probe(self) -> None:
        if self.discovery is not None:
            self.scan_label.setText("scanning…")
            self.discovery.probe()

    # -- settings --------------------------------------------------------------------------
    def _load_settings(self, prefill: ConnectParams | None) -> None:
        s = self.settings
        if prefill is not None:
            self.username.setText(prefill.username)
            self.host.setText(prefill.host)
            self.port.setValue(int(prefill.port))
            self.room.setText(prefill.room)
            self.room_name.setText(prefill.room_name or "")
            self.mode.set_index(1 if prefill.create else 0)
            self.start_server.setChecked(prefill.start_server)
            return
        import getpass
        try:
            default_user = getpass.getuser()[: C.MAX_USERNAME]
        except Exception:
            default_user = ""
        self.username.setText(str(s.value("connect/username", default_user) or ""))
        self.host.setText(str(s.value("connect/host", "127.0.0.1") or "127.0.0.1"))
        try:
            self.port.setValue(int(s.value("connect/port", C.TCP_PORT)))
        except (TypeError, ValueError):
            self.port.setValue(C.TCP_PORT)
        self.room.setText(str(s.value("connect/room", "") or ""))
        self.room_name.setText(str(s.value("connect/room_name", "") or ""))
        self.mode.set_index(1 if str(s.value("connect/mode", "join")) == "host" else 0)
        self.start_server.setChecked(str(s.value("connect/start_server", "false")).lower() == "true")

    def _save_settings(self, p: ConnectParams) -> None:
        s = self.settings
        s.setValue("connect/username", p.username)
        if not p.start_server:
            s.setValue("connect/host", p.host)
        s.setValue("connect/port", p.port)
        s.setValue("connect/room", p.room)
        s.setValue("connect/room_name", p.room_name or "")
        s.setValue("connect/mode", "host" if p.create else "join")
        s.setValue("connect/start_server", "true" if p.start_server else "false")

    # -- mode ------------------------------------------------------------------------------
    def is_host_mode(self) -> bool:
        return self.mode.index() == 1

    def _on_mode(self, idx: int) -> None:
        host = idx == 1
        self.heading.setText("Host a new whiteboard" if host else "Join a whiteboard")
        self.ok_btn.setText("Create room" if host else "Join room")
        for w in (self.gen_btn, self.room_name, self.name_caption, self.start_server):
            w.setVisible(host)
        self.password.setPlaceholderText("Optional: protect the room with a password" if host
                                         else "Only if the room is protected (you will be asked otherwise)")
        if host and not self.room.text().strip():
            self.room.setText(generate_room_code())
        self._on_start_server(self.start_server.isChecked())

    def _on_start_server(self, on: bool) -> None:
        local = on and self.is_host_mode()
        self.host.setEnabled(not local)
        if local:
            self.host.setText("127.0.0.1")
        self._update_share()

    def _update_share(self) -> None:
        local = self.start_server.isChecked() and self.is_host_mode()
        self.share.setVisible(local)
        if not local:
            return
        port = self.port.value()
        code = self.room.text().strip() or "…"
        if self.lan_addresses:
            addrs = "<br>".join(f"<b style='font-family:Menlo,Consolas,monospace'>{a}</b> : {port}"
                                for a in self.lan_addresses[:3])
            self.share.setText(f"Others on your network connect to:<br>{addrs}<br>room code <b>{code}</b>")
        else:
            self.share.setText("No LAN address found. Connect this computer to the network (Wi-Fi or "
                               "Ethernet) so others can reach it.")

    # -- discovery list ------------------------------------------------------------------------
    def set_servers(self, servers: list) -> None:
        """Slot for ``servers_changed``: rebuild the nearby-servers tree."""
        self.servers = list(servers or [])
        t = theme.tokens()
        self.tree.clear()
        if not self.servers:
            it = QTreeWidgetItem(["No servers found yet — or type an address below.", ""])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(0, QColor(t["muted"]))
            self.tree.addTopLevelItem(it)
            self.scan_label.setText("scanning…" if self.discovery is not None else "discovery unavailable")
            return
        self.scan_label.setText(f"{len(self.servers)} found")
        bold = QFont(self.tree.font())
        bold.setBold(True)
        for s in self.servers:
            top = QTreeWidgetItem([str(s.get("name") or s.get("host")), f"{s.get('host')}:{s.get('port')}"])
            top.setIcon(0, theme.icon("server", t["accent"], 16))
            top.setFont(0, bold)
            top.setForeground(1, QColor(t["muted"]))
            top.setData(0, Qt.ItemDataRole.UserRole, {"server": s})
            self.tree.addTopLevelItem(top)
            rooms = s.get("rooms") or []
            for r in rooms:
                label = r.get("name") or r.get("code")
                users = r.get("users", 0)
                detail = f"{r.get('code')} · {users} online" + (" · locked" if r.get("locked") else "")
                child = QTreeWidgetItem([str(label), detail])
                child.setIcon(0, theme.icon("lock_small" if r.get("protected") else "dot",
                                            t["warn"] if r.get("protected") else t["success"], 16))
                child.setToolTip(0, "Password protected" if r.get("protected") else "Open room")
                child.setForeground(1, QColor(t["text2"]))
                child.setData(0, Qt.ItemDataRole.UserRole, {"server": s, "room": r})
                top.addChild(child)
            if not rooms:
                empty = QTreeWidgetItem(["no rooms yet — host one", ""])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                empty.setForeground(0, QColor(t["muted"]))
                top.addChild(empty)
            top.setExpanded(True)
        self.tree.resizeColumnToContents(1)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d:
            return
        s = d["server"]
        self.start_server.setChecked(False)
        self.host.setText(str(s.get("host", "")))
        try:
            self.port.setValue(int(s.get("port", C.TCP_PORT)))
        except (TypeError, ValueError):
            pass
        r = d.get("room")
        if r is not None:
            self.mode.set_index(0)
            self.room.setText(str(r.get("code", "")))
            if r.get("protected"):
                self.password.setFocus()
        self.error.clear()

    def _on_tree_double(self, item: QTreeWidgetItem, col: int) -> None:
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if d and d.get("room"):
            self._on_tree_click(item, col)
            if not d["room"].get("protected"):
                self._on_ok()

    # -- validation + accept ---------------------------------------------------------------------
    def collect(self) -> ConnectParams:
        host_mode = self.is_host_mode()
        local = host_mode and self.start_server.isChecked()
        return ConnectParams(
            host="127.0.0.1" if local else self.host.text().strip(),
            port=int(self.port.value()),
            username=self.username.text().strip(),
            room=self.room.text().strip(),
            create=host_mode,
            password=self.password.text() or None,
            room_name=(self.room_name.text().strip() or None) if host_mode else None,
            start_server=local,
            lan_addresses=list(self.lan_addresses) if local else [],
        )

    def validate(self) -> str | None:
        """Return an error message, or None when the form is valid. Marks bad fields."""
        p = self.collect()
        problems: list[tuple[QLineEdit, str]] = []
        if not p.username:
            problems.append((self.username, "Please enter your name."))
        elif len(p.username) > C.MAX_USERNAME:
            problems.append((self.username, f"Names are at most {C.MAX_USERNAME} characters."))
        if not p.host or not HOST_RE.match(p.host):
            problems.append((self.host, "Enter the server's IP address or hostname."))
        if not 1 <= p.port <= 65535:
            problems.append((self.port, "Enter a port between 1 and 65535."))
        if not p.room:
            problems.append((self.room, "Enter a room code." if not p.create else "Choose a room code."))
        elif not ROOM_CODE_RE.match(p.room):
            problems.append((self.room, "Room codes use letters, digits and - _ . : only."))
        for w in (self.username, self.host, self.port, self.room):
            w.setProperty("invalid", any(w is pw for pw, _ in problems))
            theme.repolish(w)
        if problems:
            problems[0][0].setFocus()
            return problems[0][1]
        return None

    def _on_ok(self) -> None:
        err = self.validate()
        if err:
            self.error.setText(err)
            return
        self.error.clear()
        p = self.collect()
        if p.start_server:
            self._launch_server(p)
            return
        self._finish(p)

    def _launch_server(self, p: ConnectParams) -> None:
        self._set_busy(True, f"Starting a server on port {p.port}…")
        sp = ServerProcess(p.port)
        self.server_process = sp

        def ready() -> None:
            p.server = sp
            self._set_busy(False)
            self._finish(p)

        def failed(msg: str) -> None:
            self._set_busy(False)
            self.server_process = None
            self.error.setText(msg)

        sp.ready.connect(ready)
        sp.failed.connect(failed)
        sp.start()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.ok_btn.setEnabled(not busy)
        self.ok_btn.setText(text and "Starting server…" if busy else
                            ("Create room" if self.is_host_mode() else "Join room"))
        if busy:
            self.error.setProperty("role", "muted")
            self.error.setText(text)
        else:
            self.error.setProperty("role", "error")
        theme.repolish(self.error)

    def _finish(self, p: ConnectParams) -> None:
        self._result = p
        self._save_settings(p)
        self.accept()

    def params(self) -> ConnectParams | None:
        """The accepted parameters (None if the dialog was cancelled)."""
        return self._result
