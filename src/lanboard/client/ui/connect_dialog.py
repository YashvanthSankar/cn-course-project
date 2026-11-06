"""Welcome / connect screen.

Left: :class:`NetworkHero`, a brand panel with a gradient and a small animated
illustration of the protocol: client nodes send packets to a central server
node, which orders them (its seq counter ticks) and fans them back out. It is
painted with QPainter from a cached background pixmap, runs at most ~30 fps and
stops whenever the dialog is hidden.

Right: the form.

* **Your name**, remembered with QSettings (with a completer of recent names).
* **Join room / Host new room** segmented toggle.
* **Nearby servers** (join mode): live list from ``DiscoveryBrowser.servers_changed``
  (UDP broadcast discovery), painted as cards. Each server shows its name,
  ``host:port``, the number of rooms and people and its latency when the
  discovery entry carries one; its rooms are listed underneath (lock icon when
  password protected). Clicking a room fills the fields; double-clicking joins.
* **Recent** chips: the last servers/rooms joined from this computer.
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

import json
import random
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QCompleter, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from lanboard.common import config as C

from . import theme
from .server_launcher import ServerProcess, lan_ipv4_addresses
from .topbar import readable_on
from .widgets import PortEdit, Segmented, ToggleSwitch

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I lookalikes
ROOM_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,%d}$" % C.MAX_ROOM_CODE)
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:_\[\]%]+$")
RECENT_MAX = 4
DATA_ROLE = Qt.ItemDataRole.UserRole


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


# ---------------------------------------------------------------------------
# animated brand panel
# ---------------------------------------------------------------------------

#: client nodes of the illustration: (x, y) as fractions of the panel, name, colour
HERO_NODES = [(0.16, 0.58, "Asha", "#ffd166"), (0.80, 0.54, "Ravi", "#06d6a0"),
              (0.12, 0.80, "", "#ff7aa8"), (0.87, 0.78, "Meera", "#7cc4ff"),
              (0.33, 0.90, "", "#ffb37a"), (0.66, 0.91, "Lee", "#c3a6ff")]
HERO_SERVER = (0.50, 0.71)


class NetworkHero(QWidget):
    """Gradient brand panel with packets flowing between client nodes and a server."""

    FPS_MS = 33                     # ~30 fps cap

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(340)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._bg: QPixmap | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(self.FPS_MS)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._rng = random.Random(7)
        self.packets: list[dict] = []    # {node, up, t, speed}
        self.pings: list[list] = []      # [x, y, age, colour]
        self.seq = 1040
        self._spawn_in = 0.0
        for i in range(5):               # start mid-flow so the first frame already looks alive
            self._spawn(t=self._rng.random())
        theme.manager().changed.connect(self._invalidate)

    # -- lifecycle: animate only while visible ------------------------------------
    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._clock.restart()
        self._timer.start()

    def hideEvent(self, ev) -> None:  # noqa: N802
        super().hideEvent(ev)
        self._timer.stop()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._bg = None

    def _invalidate(self, *_args) -> None:
        self._bg = None
        self.update()

    # -- simulation ------------------------------------------------------------------
    def _spawn(self, t: float = 0.0) -> None:
        node = self._rng.randrange(len(HERO_NODES))
        self.packets.append({"node": node, "up": True, "t": t, "speed": 0.55 + self._rng.random() * 0.35})

    def _pt(self, frac: tuple[float, float]) -> QPointF:
        return QPointF(frac[0] * self.width(), frac[1] * self.height())

    def _tick(self) -> None:
        dt = min(0.1, self._clock.restart() / 1000.0)
        self._spawn_in -= dt
        if self._spawn_in <= 0:
            self._spawn()
            self._spawn_in = 0.35 + self._rng.random() * 0.4
        alive = []
        for pk in self.packets:
            pk["t"] += dt * pk["speed"]
            if pk["t"] < 1.0:
                alive.append(pk)
                continue
            if pk["up"]:
                # the server orders the op and broadcasts it to two other clients
                self.seq += 1
                self.pings.append([*HERO_SERVER, 0.0, "#ffffff"])
                others = [i for i in range(len(HERO_NODES)) if i != pk["node"]]
                for i in self._rng.sample(others, 2):
                    alive.append({"node": i, "up": False, "t": 0.0, "speed": 0.8 + self._rng.random() * 0.3})
            else:
                x, y, _n, col = HERO_NODES[pk["node"]]
                self.pings.append([x, y, 0.0, col])
        self.packets = alive[-40:]
        for pg in self.pings:
            pg[2] += dt
        self.pings = [pg for pg in self.pings if pg[2] < 0.6]
        self.update()

    # -- painting ---------------------------------------------------------------------
    def _paint_background(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        w, h = r.width(), r.height()
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor("#5b5cf0"))
        g.setColorAt(0.5, QColor("#7a4fe6"))
        g.setColorAt(1.0, QColor("#1e1a4d"))
        p.fillRect(r, g)
        glow = QRadialGradient(QPointF(w * HERO_SERVER[0], h * HERO_SERVER[1]), w * 0.75)
        glow.setColorAt(0.0, QColor(255, 255, 255, 46))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(r, glow)
        # dot grid: the "whiteboard" texture
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        step = 22
        for gx in range(step // 2, int(w), step):
            for gy in range(int(h * 0.42), int(h), step):
                p.drawEllipse(QPointF(gx, gy), 1.1, 1.1)
        # edges client <-> server
        sp = self._pt(HERO_SERVER)
        p.setPen(QPen(QColor(255, 255, 255, 55), 1.4, Qt.PenStyle.DashLine))
        for x, y, _n, _c in HERO_NODES:
            p.drawLine(self._pt((x, y)), sp)
        # logo + text
        theme.paint_logo(p, QRectF(40, 42, 52, 52))
        p.setPen(QColor("white"))
        p.setFont(theme.ui_font(32, QFont.Weight.Bold))
        p.drawText(QRectF(40, 108, w - 60, 44), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "LANBoard")
        p.setFont(theme.ui_font(14))
        p.setPen(QColor(255, 255, 255, 225))
        p.drawText(QRectF(40, 154, w - 76, 48), Qt.TextFlag.TextWordWrap,
                   "Draw together in real time on your local network. No internet, no accounts.")
        bullets = ["Finds servers on your LAN automatically",
                   "Every frame visible in the Network Inspector",
                   "Survives delay, loss and a cut cable"]
        y = 216.0
        p.setFont(theme.ui_font(13, QFont.Weight.Medium))
        for b in bullets:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawEllipse(QRectF(40, y, 20, 20))
            theme.icon("check", "#b9ffd9", 14).paint(p, QRectF(43, y + 3, 14, 14).toRect())
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(QRectF(70, y, w - 100, 20), Qt.AlignmentFlag.AlignVCenter, b)
            y += 30
        p.end()
        return pm

    def paintEvent(self, _ev) -> None:  # noqa: N802
        if self._bg is None or self._bg.size() != self.size() * self.devicePixelRatioF():
            self._bg = self._paint_background()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._bg)
        sp = self._pt(HERO_SERVER)
        # pings (expanding rings where a packet arrived)
        for x, y, age, col in self.pings:
            c = QColor(col)
            c.setAlphaF(max(0.0, 0.7 * (1 - age / 0.6)))
            p.setPen(QPen(c, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rad = 10 + age * 40
            p.drawEllipse(self._pt((x, y)), rad, rad)
        # packets with a short comet tail
        p.setPen(Qt.PenStyle.NoPen)
        for pk in self.packets:
            x, y, _n, col = HERO_NODES[pk["node"]]
            a, b = (self._pt((x, y)), sp) if pk["up"] else (sp, self._pt((x, y)))
            for k, (dt_, rad, al) in enumerate(((0.0, 4.2, 255), (0.04, 3.2, 120), (0.08, 2.4, 60))):
                tt = max(0.0, pk["t"] - dt_)
                c = QColor(col if pk["up"] else "#ffffff")
                c.setAlpha(al)
                p.setBrush(c)
                p.drawEllipse(a + (b - a) * tt, rad, rad)
        # client nodes with name tags
        f = theme.ui_font(11, QFont.Weight.Bold)
        p.setFont(f)
        for x, y, name, col in HERO_NODES:
            c = self._pt((x, y))
            p.setPen(QPen(QColor(255, 255, 255, 230), 2))
            p.setBrush(QColor(col))
            p.drawEllipse(c, 9, 9)
            if name:
                tw = p.fontMetrics().horizontalAdvance(name) + 14
                tx = c.x() + 12 if c.x() + 12 + tw < self.width() - 8 else c.x() - 12 - tw
                tag = QRectF(tx, c.y() - 26, tw, 19)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(col))
                p.drawRoundedRect(tag, 9.5, 9.5)
                p.setPen(readable_on(QColor(col)))
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, name)
        # the server: a glowing tile with the live seq counter
        tile = QRectF(sp.x() - 34, sp.y() - 26, 68, 52)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(tile.adjusted(-6, -6, 6, 6), 18, 18)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(tile, 14, 14)
        theme.icon("server", "#5b5cf0", 20).paint(p, QRectF(sp.x() - 10, tile.top() + 7, 20, 20).toRect())
        p.setFont(theme.mono_font(10, QFont.Weight.Bold))
        p.setPen(QColor("#2c2a6b"))
        p.drawText(QRectF(tile.left(), tile.top() + 28, tile.width(), 16), Qt.AlignmentFlag.AlignCenter,
                   f"seq {self.seq}")
        p.setFont(theme.ui_font(11))
        p.setPen(QColor(255, 255, 255, 160))
        p.drawText(QRectF(0, self.height() - 30, self.width() - 20, 20),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "CN Lab Project 2026 · offline")
        p.end()


# Kept for code that imported the old name.
HeroPanel = NetworkHero


# ---------------------------------------------------------------------------
# nearby-server cards
# ---------------------------------------------------------------------------

class ServerCardDelegate(QStyledItemDelegate):
    """Paints discovery results: server rows as cards, rooms as rows inside them."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        d = index.data(DATA_ROLE) or {}
        if d.get("kind") == "server":
            return QSize(option.rect.width(), 58)
        if d.get("kind") == "room":
            return QSize(option.rect.width(), 40)
        return QSize(option.rect.width(), 38)

    def paint(self, p: QPainter, option, index) -> None:
        t = theme.tokens()
        d = index.data(DATA_ROLE) or {}
        r = QRectF(option.rect).adjusted(2, 2, -2, -1)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        kind = d.get("kind")
        if kind == "server":
            s = d["server"]
            p.setPen(QPen(QColor(t["border"]), 1))
            p.setBrush(QColor(t["surface2"]))
            p.drawRoundedRect(r.adjusted(0, 4, 0, 0), 12, 12)
            ic = QRectF(r.left() + 12, r.top() + 13, 34, 34)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent_gradient(ic, t))
            p.drawRoundedRect(ic, 10, 10)
            theme.icon("server", "#ffffff", 18).paint(p, ic.adjusted(8, 8, -8, -8).toRect())
            x = ic.right() + 12
            p.setFont(theme.ui_font(14, QFont.Weight.DemiBold))
            p.setPen(QColor(t["text"]))
            p.drawText(QRectF(x, r.top() + 11, r.width() - x, 20), Qt.AlignmentFlag.AlignVCenter,
                       str(s.get("name") or s.get("host")))
            p.setFont(theme.mono_font(11))
            p.setPen(QColor(t["muted"]))
            p.drawText(QRectF(x, r.top() + 31, r.width() - x, 18), Qt.AlignmentFlag.AlignVCenter,
                       f"{s.get('host')}:{s.get('port')}")
            # right-hand chips: latency (if the entry has one) and room / people counts
            rooms = s.get("rooms") or []
            people = sum(int(rm.get("users", 0) or 0) for rm in rooms)
            chips = [(f"{len(rooms)} room{'s' if len(rooms) != 1 else ''} · {people} online", "accent")]
            lat = s.get("rtt_ms", s.get("latency_ms"))
            if isinstance(lat, (int, float)):
                chips.insert(0, (f"{lat:.0f} ms" if lat >= 10 else f"{lat:.1f} ms", "success"))
            cx = r.right() - 12
            p.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
            for text, tok in chips:
                w = p.fontMetrics().horizontalAdvance(text) + 16
                cr = QRectF(cx - w, r.top() + 20, w, 22)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.alpha(t[tok], 0.14))
                p.drawRoundedRect(cr, 11, 11)
                p.setPen(QColor(t[tok]))
                p.drawText(cr, Qt.AlignmentFlag.AlignCenter, text)
                cx -= w + 6
        elif kind == "room":
            rm = d["room"]
            rr = r.adjusted(18, 1, -4, -1)
            if sel or hover:
                p.setPen(QPen(QColor(t["accent"]), 1) if sel else Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["accent_soft"] if sel else t["surface2"]))
                p.drawRoundedRect(rr, 9, 9)
            prot = bool(rm.get("protected"))
            theme.icon("lock_small" if prot else "dot", t["warn"] if prot else t["success"], 14).paint(
                p, QRectF(rr.left() + 10, rr.center().y() - 7, 14, 14).toRect())
            p.setFont(theme.ui_font(13, QFont.Weight.Medium))
            p.setPen(QColor(t["text"]))
            name = str(rm.get("name") or rm.get("code"))
            p.drawText(QRectF(rr.left() + 32, rr.top(), rr.width() * 0.5, rr.height()), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(rr.width() * 0.5)))
            users = int(rm.get("users", 0) or 0)
            detail = f"{users} online" + (" · locked" if rm.get("locked") else "")
            p.setFont(theme.ui_font(11))
            p.setPen(QColor(t["muted"]))
            dw = p.fontMetrics().horizontalAdvance(detail)
            p.drawText(QRectF(rr.right() - dw - 10, rr.top(), dw + 2, rr.height()), Qt.AlignmentFlag.AlignVCenter, detail)
            code = str(rm.get("code", ""))
            p.setFont(theme.mono_font(11, QFont.Weight.Bold))
            cw = p.fontMetrics().horizontalAdvance(code) + 14
            cr = QRectF(rr.right() - dw - 22 - cw, rr.center().y() - 10, cw, 20)
            p.setPen(QPen(QColor(t["border"]), 1))
            p.setBrush(QColor(t["surface"]))
            p.drawRoundedRect(cr, 6, 6)
            p.setPen(QColor(t["text2"]))
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, code)
        else:                                       # placeholder rows ("no servers yet")
            p.setFont(theme.ui_font(12))
            p.setPen(QColor(t["muted"]))
            p.drawText(r.adjusted(18 if d.get("child") else 8, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                       index.data(Qt.ItemDataRole.DisplayRole) or "")
        p.restore()


# ---------------------------------------------------------------------------
# the dialog
# ---------------------------------------------------------------------------

class ConnectDialog(QDialog):
    """The first screen. ``exec()`` then ``params()``."""

    def __init__(self, parent: QWidget | None = None, discovery=None, prefill: ConnectParams | None = None,
                 error_text: str = "", settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LANBoard — connect")
        self.setWindowIcon(theme.app_icon())
        self.setMinimumSize(QSize(960, 640))
        self.settings = settings or QSettings()
        self.servers: list[dict] = []
        self.discovery = None
        self.server_process: ServerProcess | None = None
        self._result: ConnectParams | None = None
        self.lan_addresses = lan_ipv4_addresses(lan_only=True)   # VPN/CGNAT only if nothing better

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.hero = NetworkHero()
        root.addWidget(self.hero, 5)

        right = QWidget()
        right.setObjectName("SidePage")
        right.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.addWidget(right, 7)
        lay = QVBoxLayout(right)
        lay.setContentsMargins(36, 28, 36, 24)
        lay.setSpacing(8)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.heading = QLabel("Join a whiteboard")
        self.heading.setProperty("role", "h1")
        titles.addWidget(self.heading)
        self.subheading = QLabel("")
        self.subheading.setProperty("role", "hint")
        titles.addWidget(self.subheading)
        head.addLayout(titles, 1)
        self.theme_btn = QToolButton()
        self.theme_btn.setToolTip("Toggle light / dark theme")
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.setIconSize(QSize(18, 18))
        self.theme_btn.clicked.connect(self._toggle_theme)
        head.addWidget(self.theme_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        lay.addSpacing(8)

        # username + mode
        top = QGridLayout()
        top.setHorizontalSpacing(12)
        top.setVerticalSpacing(5)
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

        # ---- join: nearby servers + recents ---------------------------------------
        self.join_box = QWidget()
        jl = QVBoxLayout(self.join_box)
        jl.setContentsMargins(0, 8, 0, 0)
        jl.setSpacing(6)
        nh = QHBoxLayout()
        nh.addWidget(self._caption("Nearby servers"))
        self.scan_label = QLabel("scanning…")
        self.scan_label.setProperty("role", "muted")
        self.scan_label.setStyleSheet("font-size: 11px;")
        nh.addWidget(self.scan_label)
        nh.addStretch(1)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setToolTip("Probe the network again")
        self.refresh_btn.clicked.connect(self._probe)
        nh.addWidget(self.refresh_btn)
        jl.addLayout(nh)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setMinimumHeight(150)
        self.tree.setMouseTracking(True)
        self.tree.setItemDelegate(ServerCardDelegate(self.tree))
        self.tree.setStyleSheet("QTreeWidget { border: none; background: transparent; }"
                                " QTreeWidget::item { padding: 0; }"
                                " QTreeWidget::item:selected, QTreeWidget::item:hover { background: transparent; }")
        self.tree.itemClicked.connect(self._on_tree_click)
        self.tree.itemDoubleClicked.connect(self._on_tree_double)
        jl.addWidget(self.tree, 1)
        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(6)
        self.recent_caption = self._caption("Recent")
        self.recent_row.addWidget(self.recent_caption)
        self.recent_chips: list[QToolButton] = []
        self.recent_row.addStretch(1)
        jl.addLayout(self.recent_row)
        lay.addWidget(self.join_box, 1)

        # ---- host: start a local server ------------------------------------------
        self.host_box = QFrame()
        self.host_box.setObjectName("Card")
        hb = QVBoxLayout(self.host_box)
        hb.setContentsMargins(16, 14, 16, 14)
        hb.setSpacing(8)
        srow = QHBoxLayout()
        self.server_icon = QLabel()
        self.server_icon.setFixedSize(34, 34)
        self.server_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        srow.addWidget(self.server_icon)
        stext = QVBoxLayout()
        stext.setSpacing(0)
        st_title = QLabel("Start a server on this computer")
        st_title.setStyleSheet("font-weight: 600;")
        stext.addWidget(st_title)
        st_sub = QLabel("Runs lanboard-server in the background. Leave off if a server already runs on the LAN.")
        st_sub.setProperty("role", "hint")
        st_sub.setWordWrap(True)
        stext.addWidget(st_sub)
        srow.addLayout(stext, 1)
        self.start_server = ToggleSwitch()
        self.start_server.toggled.connect(self._on_start_server)
        srow.addWidget(self.start_server)
        hb.addLayout(srow)
        self.share = QLabel()
        self.share.setProperty("role", "card")
        self.share.setWordWrap(True)
        self.share.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hb.addWidget(self.share)
        self.host_wrap = QWidget()
        hwl = QVBoxLayout(self.host_wrap)
        hwl.setContentsMargins(0, 8, 0, 0)
        hwl.addWidget(self.host_box)
        lay.addWidget(self.host_wrap)

        # ---- shared fields ---------------------------------------------------------
        hp = QGridLayout()
        hp.setHorizontalSpacing(12)
        hp.setVerticalSpacing(5)
        hp.addWidget(self._caption("Server address"), 0, 0)
        hp.addWidget(self._caption("Port"), 0, 1)
        self.host = QLineEdit("127.0.0.1")
        self.host.setPlaceholderText("e.g. 192.168.1.20")
        hp.addWidget(self.host, 1, 0)
        self.port = PortEdit(C.TCP_PORT)
        self.port.setFixedWidth(100)
        self.port.valueChanged.connect(lambda *_: self._update_share())
        self.port.textEdited.connect(lambda *_: self._host_typed_now("port", self.port.value()))
        hp.addWidget(self.port, 1, 1)
        hp.setColumnStretch(0, 1)
        lay.addLayout(hp)

        rf = QGridLayout()
        self._room_grid = rf
        rf.setHorizontalSpacing(12)
        rf.setVerticalSpacing(5)
        rf.addWidget(self._caption("Room code"), 0, 0)
        self.name_caption = self._caption("Room name")
        rf.addWidget(self.name_caption, 0, 1)
        self.pw_caption = self._caption("Password")
        rf.addWidget(self.pw_caption, 0, 2)
        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self.room = QLineEdit()
        self.room.setPlaceholderText("e.g. K7Q-2MX")
        self.room.setMaxLength(C.MAX_ROOM_CODE)
        self.room.textChanged.connect(lambda *_: self._update_share())
        self.room.textEdited.connect(lambda t: self._host_typed_now("code", t.strip()))
        code_row.addWidget(self.room, 1)
        self.gen_btn = QToolButton()
        self.gen_btn.setToolTip("Generate a fresh room code")
        self.gen_btn.setFixedSize(34, 34)
        self.gen_btn.clicked.connect(lambda: self.room.setText(generate_room_code()))
        code_row.addWidget(self.gen_btn)
        rf.addLayout(code_row, 1, 0)
        self.room_name = QLineEdit()
        self.room_name.setPlaceholderText("e.g. CN Lab — Group 4")
        self.room_name.setMaxLength(64)
        rf.addWidget(self.room_name, 1, 1)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        rf.addWidget(self.password, 1, 2)
        rf.setColumnStretch(0, 1)
        rf.setColumnStretch(1, 1)
        rf.setColumnStretch(2, 1)
        lay.addLayout(rf)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)
        # in host mode there is no server list, so this spacer keeps the buttons at the bottom
        self.host_spacer = QWidget()
        self.host_spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.host_spacer, 1)

        btns = QHBoxLayout()
        self.lan_hint = QLabel("")
        self.lan_hint.setProperty("role", "muted")
        self.lan_hint.setStyleSheet("font-size: 11px;")
        btns.addWidget(self.lan_hint)
        btns.addStretch(1)
        self.cancel_btn = QPushButton("Quit")
        self.cancel_btn.setProperty("role", "ghost")
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        self.ok_btn = QPushButton("Join room")
        self.ok_btn.setProperty("role", "primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.setMinimumWidth(170)
        self.ok_btn.setMinimumHeight(40)
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.clicked.connect(self._on_ok)
        btns.addWidget(self.ok_btn)
        lay.addLayout(btns)
        if self.lan_addresses:
            self.lan_hint.setText(f"This computer: {self.lan_addresses[0]}")

        # port / room code the user typed by hand while in host mode; switching to
        # Host restores these (or the defaults), never a discovered server's values
        self._host_typed: dict = {"port": None, "code": None}
        self._mode_ready = False
        self._load_settings(prefill)
        self._build_recents()
        self._on_mode(self.mode.index())
        if self.is_host_mode():                     # values saved from a previous host session
            self._host_typed = {"port": self.port.value() or None, "code": self.room.text().strip() or None}
        self._mode_ready = True
        self.error.setText(error_text)
        self._retheme_icons()
        theme.manager().changed.connect(self._retheme_icons)

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

    def _retheme_icons(self, *_args) -> None:
        t = theme.tokens()
        self.theme_btn.setIcon(theme.icon("sun" if theme.manager().dark else "moon", t["text2"], 18))
        self.refresh_btn.setIcon(theme.icon("refresh", t["text2"], 16))
        self.gen_btn.setIcon(theme.icon("smart", t["accent"], 16))
        self.server_icon.setPixmap(theme.icon("server", t["accent"], 18).pixmap(18, 18))
        self.server_icon.setStyleSheet(f"background: {t['accent_soft']}; border-radius: 10px;")
        self.tree.viewport().update()

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
        names = self._recent_names()
        if names:
            comp = QCompleter(names, self)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.username.setCompleter(comp)
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

    def _json_list(self, key: str) -> list:
        try:
            v = json.loads(str(self.settings.value(key, "[]") or "[]"))
            return v if isinstance(v, list) else []
        except (TypeError, ValueError):
            return []

    def _recent_names(self) -> list[str]:
        return [str(n) for n in self._json_list("connect/recent_names") if isinstance(n, str)]

    def recents(self) -> list[dict]:
        """Recently used ``{host, port, room}`` entries, newest first."""
        out = []
        for r in self._json_list("connect/recent"):
            if isinstance(r, dict) and r.get("host") and r.get("room"):
                out.append({"host": str(r["host"]), "port": int(r.get("port") or C.TCP_PORT), "room": str(r["room"])})
        return out[:RECENT_MAX]

    def _build_recents(self) -> None:
        for chip in self.recent_chips:
            chip.deleteLater()
        self.recent_chips = []
        recents = self.recents()
        self.recent_caption.setVisible(bool(recents))
        for i, r in enumerate(recents):
            chip = QToolButton()
            chip.setProperty("role", "chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setText(f"{r['host']} · {r['room']}")
            chip.setToolTip(f"Fill in {r['host']}:{r['port']}, room {r['room']}")
            chip.clicked.connect(lambda _=False, rr=r: self.use_recent(rr))
            self.recent_row.insertWidget(1 + i, chip)
            self.recent_chips.append(chip)

    def use_recent(self, r: dict) -> None:
        self.mode.set_index(0)
        self.host.setText(r["host"])
        self.port.setValue(int(r["port"]))
        self.room.setText(r["room"])
        self.error.clear()

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
        names = [p.username] + [n for n in self._recent_names() if n != p.username]
        s.setValue("connect/recent_names", json.dumps(names[:8]))
        host = (p.lan_addresses[0] if p.lan_addresses else p.host) if p.start_server else p.host
        entry = {"host": host, "port": p.port, "room": p.room}
        rec = [entry] + [r for r in self.recents() if (r["host"], r["port"], r["room"]) != (host, p.port, p.room)]
        s.setValue("connect/recent", json.dumps(rec[:RECENT_MAX]))

    # -- mode ------------------------------------------------------------------------------
    def is_host_mode(self) -> bool:
        return self.mode.index() == 1

    def _on_mode(self, idx: int) -> None:
        host = idx == 1
        self.heading.setText("Host a new whiteboard" if host else "Join a whiteboard")
        self.subheading.setText("Create a room others on your network can join." if host
                                else "Pick a server found on your network, or type its address.")
        self.ok_btn.setText("Create room" if host else "Join room")
        for w in (self.gen_btn, self.room_name, self.name_caption):
            w.setVisible(host)
        self._room_grid.setColumnStretch(1, 1 if host else 0)     # no empty gap in join mode
        self.join_box.setVisible(not host)
        self.host_wrap.setVisible(host)
        self.host_spacer.setVisible(host)
        self.password.setPlaceholderText("Optional" if host else "Only if protected")
        if host and self._mode_ready:
            # a discovered server's port and room code belong to that server: host fresh
            self.port.setValue(self._host_typed["port"] or C.TCP_PORT)
            self.room.setText(self._host_typed["code"] or generate_room_code())
        elif host and not self.room.text().strip():
            self.room.setText(generate_room_code())
        self._on_start_server(self.start_server.isChecked())

    def _host_typed_now(self, key: str, value) -> None:
        if self.is_host_mode():
            self._host_typed[key] = value or None

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
        mono = theme.mono_css().replace('"', "'")
        if self.lan_addresses:
            addrs = "<br>".join(f"<span style=\"font-family:{mono}; font-size:17px\"><b>{a}</b> : {port}</span>"
                                for a in self.lan_addresses[:3])
            self.share.setText(f"Others on your network connect to<br>{addrs}<br>room code "
                               f"<b style=\"font-family:{mono}\">{code}</b>")
        else:
            self.share.setText("No LAN address found. Connect this computer to the network (Wi-Fi or "
                               "Ethernet) so others can reach it.")

    # -- discovery list ------------------------------------------------------------------------
    def set_servers(self, servers: list) -> None:
        """Slot for ``servers_changed``: rebuild the nearby-servers list."""
        self.servers = list(servers or [])
        self.tree.clear()
        if not self.servers:
            it = QTreeWidgetItem(["No servers found yet. Make sure you are on the same network, or type an address."])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setData(0, DATA_ROLE, {"kind": "empty"})
            self.tree.addTopLevelItem(it)
            self.scan_label.setText("scanning…" if self.discovery is not None else "discovery unavailable")
            return
        self.scan_label.setText(f"{len(self.servers)} found")
        for s in self.servers:
            top = QTreeWidgetItem([str(s.get("name") or s.get("host"))])
            top.setData(0, DATA_ROLE, {"kind": "server", "server": s})
            top.setToolTip(0, f"{s.get('host')}:{s.get('port')}  (click to use this server)")
            self.tree.addTopLevelItem(top)
            rooms = s.get("rooms") or []
            for r in rooms:
                child = QTreeWidgetItem([str(r.get("name") or r.get("code"))])
                child.setToolTip(0, "Password protected" if r.get("protected") else "Open room. Double-click to join")
                child.setData(0, DATA_ROLE, {"kind": "room", "server": s, "room": r})
                top.addChild(child)
            if not rooms:
                empty = QTreeWidgetItem(["No rooms yet: switch to Host new room to create one."])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                empty.setData(0, DATA_ROLE, {"kind": "empty", "child": True})
                top.addChild(empty)
            top.setExpanded(True)

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        d = item.data(0, DATA_ROLE)
        if not d or "server" not in d:
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
        d = item.data(0, DATA_ROLE)
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


