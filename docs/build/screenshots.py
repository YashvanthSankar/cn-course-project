"""Render real LANBoard screenshots for the docs and the presentation.

Everything in the pictures is real:

* the main-window shots talk to ``python -m lanboard.server`` started as a
  subprocess on a free TCP port (``--no-discovery``), exactly as a user runs it;
* the other participants are real ``NetClient`` + ``SessionController`` clients;
* the windows are the real ``MainWindow`` / ``ConnectDialog`` widgets, rendered
  offscreen with ``QWidget.grab()``;
* the connect dialog uses the client's own ``DiscoveryBrowser``: an in-process
  ``Server`` answers discovery on a free UDP port and the browser probes it
  (nothing is injected into the "Nearby servers" list);
* the server terminal dashboard is the actual output of
  ``python -m lanboard.server --dashboard``, painted into a terminal-style PNG.

Usage (from the repository root, with the project venv)::

    .venv/bin/python docs/build/screenshots.py [OUTDIR] [--only name ...]

OUTDIR defaults to ``docs/assets/screens``. Environment:

* ``GRAB_SIZE``  logical window size, default ``1440x900``
* ``GRAB_SCALE`` device pixel ratio, default ``2`` (2880x1800 PNGs)

The default LANBoard ports (TCP 5050, UDP 5051) are never used, so this can run
next to a real server.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

ap = argparse.ArgumentParser()
ap.add_argument("outdir", nargs="?", default=os.path.join(ROOT, "docs", "assets", "screens"))
ap.add_argument("--only", nargs="*", default=None, help="subset of shot names (see SHOTS)")
ARGS = ap.parse_args()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", os.environ.get("GRAB_SCALE", "2"))

from PySide6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize,  # noqa: E402
                           QPainter, QPainterPath, QPdfWriter, QPixmap)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

app = QApplication(sys.argv[:1])
app.setOrganizationName("LANBoardScreens")
app.setApplicationName("LANBoardScreens")

from lanboard.client.ui import theme  # noqa: E402

theme.load_fonts()
theme.apply_theme(app, "light")

from lanboard.client.controller import SessionController  # noqa: E402
from lanboard.client.discovery import DiscoveryBrowser  # noqa: E402
from lanboard.client.net import NetClient  # noqa: E402
from lanboard.client.ui.connect_dialog import ConnectDialog  # noqa: E402
from lanboard.client.ui.main_window import MainWindow  # noqa: E402
from lanboard.server import Server  # noqa: E402
from lanboard.server.discovery import lan_ipv4_addresses  # noqa: E402

OUT = os.path.abspath(ARGS.outdir)
os.makedirs(OUT, exist_ok=True)
W, H = (int(x) for x in os.environ.get("GRAB_SIZE", "1440x900").split("x"))
ROOM, ROOM_NAME = "CN-2026", "CN Lab: Group 4"
SECRET_ROOM, SECRET_PW = "VIVA-7", "viva-secret"
SERVER_NAME = "CN-Lab-Server"
saved: list[str] = []


def want(name: str) -> bool:
    return ARGS.only is None or name in ARGS.only


def wait(ms: int) -> None:
    end = time.time() + ms / 1000
    while time.time() < end:
        QTest.qWait(15)


def save(pix: QPixmap | QImage, name: str) -> None:
    path = os.path.join(OUT, name)
    pix.save(path)
    saved.append(name)
    print("saved", name, f"{pix.width()}x{pix.height()}", flush=True)


def tmp_settings() -> QSettings:
    return QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


LAN = [ip for ip in lan_ipv4_addresses() if not ip.startswith("169.254.")]
HOST = LAN[0] if LAN else "127.0.0.1"
CHILD_ENV = dict(os.environ, PYTHONUNBUFFERED="1")
CHILD_ENV.pop("QT_QPA_PLATFORM", None)
CHILD_ENV.pop("QT_SCALE_FACTOR", None)
CHILD_ENV["PYTHONPATH"] = os.path.join(ROOT, "src")


def start_cli_server(*extra: str) -> tuple[subprocess.Popen, int, str]:
    """``python -m lanboard.server`` exactly as a user runs it (own process, so the
    GUI rendering in this process does not distort RTTs). No discovery: the CLI
    can only answer on the default UDP port 5051, which belongs to the user's app."""
    port = free_port()
    data = tempfile.mkdtemp(prefix="lanboard-shots-")
    log_path = os.path.join(data, "server.out")
    cmd = [sys.executable, "-m", "lanboard.server", "--host", "0.0.0.0", "--port", str(port),
           "--no-discovery", "--data", data, "--name", SERVER_NAME, *extra]
    fh = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=CHILD_ENV, cwd=ROOT)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return proc, port, log_path


def start_discovery_server() -> Server:
    """An in-process Server with discovery on a free UDP port, for the connect dialog."""
    loop = asyncio.new_event_loop()
    srv = Server("0.0.0.0", 0, tempfile.mkdtemp(prefix="lanboard-disc-"), SERVER_NAME,
                 discovery=True, discovery_port=0)
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.start())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    assert srv.port != 5050 and srv.discovery_port != 5051
    return srv


SERVER_PROC, PORT, _ = start_cli_server()
print(f"server {SERVER_NAME} on {HOST}:{PORT}", flush=True)


def make_client(name: str, room: str = ROOM, create: bool = False, password: str | None = None,
                room_name: str | None = None, port: int | None = None):
    n = NetClient()
    c = SessionController(n)
    n.connect_to(HOST, port or PORT, name, room, create=create, password=password, room_name=room_name)
    return n, c


def make_pdf(path: str) -> None:
    pdf = QPdfWriter(path)
    pdf.setPageSize(QPageSize(QPageSize.PageSizeId.A5))
    pdf.setPageOrientation(QPageLayout.Orientation.Landscape)
    pdf.setResolution(144)
    p = QPainter(pdf)
    w, h = pdf.width(), pdf.height()
    p.fillRect(0, 0, w, h, QColor("#ffffff"))
    p.fillRect(0, 0, w, int(h * 0.2), QColor("#1f57c3"))
    f = QFont(theme.UI_FAMILY or "Inter")
    f.setPixelSize(int(h * 0.075)); f.setBold(True)
    p.setFont(f); p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(w * 0.05, 0, w * 0.9, h * 0.2), Qt.AlignmentFlag.AlignVCenter, "Lecture 7: TCP framing")
    f.setPixelSize(int(h * 0.045)); f.setBold(False)
    p.setFont(f); p.setPen(QColor("#1b2130"))
    lines = ["TCP is a byte stream: it has no message boundaries.",
             "So every message is length-prefixed:",
             "",
             "    | length (u32, big-endian) | type (u8) | JSON payload |",
             "",
             "The decoder buffers partial reads and may find",
             "several frames in one recv().",
             "Max frame 8 MiB: anything larger closes the link."]
    y = h * 0.27
    for ln in lines:
        p.drawText(QPointF(w * 0.06, y), ln)
        y += h * 0.075
    p.end()


def shoot_connect() -> None:
    server = start_discovery_server()
    print(f"discovery server on TCP {server.port}, UDP {server.discovery_port}", flush=True)
    people = [make_client("Yashvanth", ROOM, create=True, room_name=ROOM_NAME, port=server.port)]
    wait(500)
    people += [make_client(nm, port=server.port) for nm in ("Jeeva", "Sai Teja", "Nithilla", "Asha", "Ravi")]
    people.append(make_client("Karthik", SECRET_ROOM, create=True, password=SECRET_PW,
                              room_name="Viva prep", port=server.port))
    wait(1200)
    for mode in ("light", "dark"):
        theme.apply_theme(app, mode)
        st = tmp_settings()
        st.setValue("connect/recent", f'[{{"host": "{HOST}", "port": {server.port}, "room": "{ROOM}"}}]')
        browser = DiscoveryBrowser(port=server.discovery_port, interval_ms=500)
        dlg = ConnectDialog(discovery=browser, settings=st)
        dlg.username.setText("Yashvanth")
        dlg.resize(1000, 680)
        dlg.show()
        suffix = "" if mode == "light" else "_dark"
        dlg.mode.set_index(1)
        dlg.start_server.setChecked(True)
        wait(300)
        save(dlg.grab(), f"connect_host{suffix}.png")
        dlg.start_server.setChecked(False)
        dlg.mode.set_index(0)
        for _ in range(40):
            wait(100)
            if browser.servers():
                break
        wait(400)
        found = browser.servers()
        print("discovered:", [(s["name"], s["host"], s["port"], len(s["rooms"])) for s in found], flush=True)
        if found:
            top = dlg.tree.topLevelItem(0)
            for i in range(top.childCount() if top else 0):
                child = top.child(i)
                if (child.data(0, Qt.ItemDataRole.UserRole) or {}).get("room", {}).get("code") == ROOM:
                    dlg.tree.setCurrentItem(child)
                    dlg._on_tree_click(child)
            wait(200)
        else:
            print("!! discovery found nothing; screenshot shows the typed-address form", flush=True)
        save(dlg.grab(), f"connect{suffix}.png")
        dlg.close()
        dlg.deleteLater()
        browser.stop()
        wait(100)
    for n, _c in people:
        n.close()
    theme.apply_theme(app, "light")


def build_board(ctl, others) -> None:
    """A small architecture sketch, drawn by several real clients."""
    ink, indigo, violet = "#1b2130", "#1d4ed8", "#c2410c"
    add = ctl.add_object
    add({"type": "text", "style": {"color": ink, "font_size": 34},
         "geom": {"x": 80, "y": 40, "text": "LANBoard: how one edit travels"}})
    for i, label in enumerate(("Client A  (Qt)", "Client B  (Qt)", "Client C  (Qt)")):
        y = 150 + i * 150
        add({"type": "rect", "style": {"color": indigo, "width": 3, "fill": "#e8eefb"},
             "geom": {"x": 80, "y": y, "w": 220, "h": 96}})
        add({"type": "text", "style": {"color": ink, "font_size": 20},
             "geom": {"x": 104, "y": y + 30, "text": label}})
        add({"type": "arrow", "style": {"color": indigo, "width": 3},
             "geom": {"x1": 310, "y1": y + 48, "x2": 520, "y2": 330}})
    add({"type": "rect", "style": {"color": violet, "width": 4, "fill": "#fdf1e3"},
         "geom": {"x": 530, "y": 250, "w": 260, "h": 170}})
    add({"type": "text", "style": {"color": ink, "font_size": 22},
         "geom": {"x": 556, "y": 270, "text": "Server (asyncio)"}})
    add({"type": "text", "style": {"color": "#5b6170", "font_size": 16},
         "geom": {"x": 556, "y": 312, "text": "room actor\nboard + op log\nseq = seq + 1"}})
    add({"type": "text", "style": {"color": indigo, "font_size": 15},
         "geom": {"x": 330, "y": 210, "text": "TCP, framed JSON"}})
    add({"type": "ellipse", "style": {"color": "#30a46c", "width": 3, "fill": "#e9f9ef"},
         "geom": {"x": 560, "y": 500, "w": 200, "h": 90}})
    add({"type": "text", "style": {"color": ink, "font_size": 16},
         "geom": {"x": 598, "y": 530, "text": "ops.jsonl"}})
    add({"type": "line", "style": {"color": "#30a46c", "width": 3},
         "geom": {"x1": 660, "y1": 425, "x2": 660, "y2": 495}})
    a, b, c = others[0][1], others[1][1], others[2][1]
    a.add_object({"type": "sticky", "style": {"fill": "#fff59d", "font_size": 17},
                  "geom": {"x": 860, "y": 150, "w": 230, "h": 140,
                           "text": "Server assigns seq: one total order for everyone"}})
    b.add_object({"type": "sticky", "style": {"fill": "#ffd6e7", "font_size": 17},
                  "geom": {"x": 860, "y": 320, "w": 230, "h": 140,
                           "text": "The broadcast echo is the ACK"}})
    c.add_object({"type": "sticky", "style": {"fill": "#c8f7dc", "font_size": 17},
                  "geom": {"x": 860, "y": 490, "w": 230, "h": 140,
                           "text": "Gap in seq? Send RESYNC"}})
    pts = []
    for i in range(72):
        t = i / 66 * 2 * math.pi
        wob = 1 + 0.04 * math.sin(3 * t)
        pts += [round(660 + 168 * wob * math.cos(t), 1), round(335 + 118 * wob * math.sin(t), 1)]
    add({"type": "stroke", "style": {"color": "#e5484d", "width": 4}, "geom": {"pts": pts}})
    wave = []
    for i in range(50):
        wave += [80 + i * 8, round(700 + 10 * math.sin(i / 3), 1)]
    b.add_object({"type": "stroke", "style": {"color": "#0090ff", "width": 5}, "geom": {"pts": wave}})
    hl = []
    for i in range(30):
        hl += [80 + i * 13, 70]
    c.add_object({"type": "stroke", "style": {"color": "#ffd60a", "width": 18, "opacity": 0.35},
                  "geom": {"pts": hl}})
    add({"type": "text", "style": {"color": "#0090ff", "font_size": 15},
         "geom": {"x": 80, "y": 720, "text": "live strokes are previewed at ~30 Hz"}})


def fit(win) -> None:
    """Like View > Fit, but keeps content clear of the floating tool rail and style bar."""
    r = win.canvas.canvas.content_rect()
    win.canvas.animate_to(r.adjusted(-190, -60, 60, 150), ms=0)


def find_sticky_ids(ctl) -> list[str]:
    return [oid for oid, o in ctl.view.objects.items()
            if o.get("type") == "sticky" and not o.get("deleted")]


def main_window_shots() -> None:
    settings = tmp_settings()
    net, ctl = NetClient(), None
    ctl = SessionController(net)
    win = MainWindow(ctl, settings=settings, lan_addresses=LAN)
    win.resize(W, H)
    win.show()
    net.connect_to(HOST, PORT, "Yashvanth", ROOM, create=True, room_name=ROOM_NAME)
    wait(1200)
    others = []
    for name in ("Jeeva", "Sai Teja", "Nithilla", "Asha"):
        others.append(make_client(name))
    vnet = NetClient()
    vctl = SessionController(vnet)
    vwin = MainWindow(vctl, settings=tmp_settings(), lan_addresses=[])
    vwin.resize(W, H)
    vwin.show()
    vnet.connect_to(HOST, PORT, "Ravi", ROOM)
    others.append((vnet, vctl))
    wait(1500)
    secret = make_client("Karthik", SECRET_ROOM, create=True, password=SECRET_PW, room_name="Viva prep")
    wait(600)

    build_board(ctl, others)
    wait(1500)

    pdf_path = os.path.join(tempfile.mkdtemp(), "lecture7.pdf")
    make_pdf(pdf_path)
    win.canvas.zoom_fit()
    wait(300)
    win.canvas.import_pdf(pdf_path)
    wait(1500)
    pdf_ids = [oid for oid, o in ctl.view.objects.items() if o.get("type") == "image"]
    for oid in pdf_ids:
        g = ctl.view.objects[oid]["geom"]
        ctl.update_object(oid, {"geom.x": 1160.0, "geom.y": 150.0, "geom.w": g["w"] * 0.9, "geom.h": g["h"] * 0.9})
    wait(600)
    if pdf_ids:
        g = ctl.view.objects[pdf_ids[0]]["geom"]
        x0, y0 = g["x"], g["y"]
        others[3][1].add_object({"type": "ellipse", "style": {"color": "#e5484d", "width": 4},
                                 "geom": {"x": x0 + 30, "y": y0 + g["h"] * 0.38, "w": g["w"] * 0.86, "h": 60}})
        others[3][1].add_object({"type": "arrow", "style": {"color": "#e5484d", "width": 4},
                                 "geom": {"x1": x0 + g["w"] * 0.6, "y1": y0 + g["h"] + 70,
                                          "x2": x0 + g["w"] * 0.5, "y2": y0 + g["h"] * 0.5 + 40}})
        others[3][1].add_object({"type": "text", "style": {"color": "#e5484d", "font_size": 18},
                                 "geom": {"x": x0 + g["w"] * 0.45, "y": y0 + g["h"] + 76,
                                          "text": "this is our frame header!"}})
    wait(800)
    for sid in find_sticky_ids(ctl)[:1]:
        ctl.update_object(sid, {"votes": [ctl.user_id, others[0][0].user_id, others[1][0].user_id]})
    wait(500)

    uid = {name: n.user_id for name, (n, _c) in zip(("Jeeva", "Sai Teja", "Nithilla", "Asha", "Ravi"), others)}
    ctl.set_role(uid["Ravi"], "viewer")
    ctl.set_role(uid["Asha"], "viewer")
    others[2][1].send_signal("hand_up")
    wait(500)

    ctl.send_chat("Hi team, I've sketched the architecture on the left.")
    wait(250)
    others[0][1].send_chat("Nice. I'll explain the frame format using the PDF slide.")
    wait(250)
    others[1][1].send_chat("Every op gets a seq from the server, so all replicas converge.")
    wait(250)
    others[2][1].send_chat("Can we show the Chaos Lab after this? ✋")
    wait(250)
    others[3][1].send_chat("Circled the header on the slide 👆")
    wait(250)
    ctl.send_chat("Sure. Cut the cable first, then drop 20% of OPs.")
    wait(1200)

    fit(win)
    wait(600)

    def presence():
        for i, (_n, c) in enumerate(others[:3]):
            c.send_cursor(420 + i * 260, 640 - i * 40)
        live = others[0][1]
        pts = []
        for i in range(18):
            pts += [340 + i * 12, round(610 + 14 * math.sin(i / 2.5), 1)]
        live.send_live("live-demo", "#f76b15", 4, pts)
    presence()
    wait(700)

    def clear_toasts():
        for t in list(win.toasts._toasts):
            t.dismiss()
        wait(450)

    def shot(name, keep_toasts=False, w=win):
        if not keep_toasts:
            clear_toasts()
        presence()
        wait(300)
        if want(name.removesuffix(".png")):
            save(w.grab(), name)

    win.show_tab(win.TAB_PEOPLE)
    shot("light.png")
    if want("people_roles"):
        clear_toasts()
        pix = win.grab()
        tl = win.side.mapTo(win, QPoint(0, 0))
        dpr = pix.devicePixelRatio()
        h = min(win.side.height(), 340)
        save(pix.copy(int(tl.x() * dpr), int(tl.y() * dpr), int(win.side.width() * dpr), int(h * dpr)),
             "people_roles.png")
    win.show_tab(win.TAB_CHAT)
    shot("chat.png")

    if want("history"):
        win.show_tab(win.TAB_HISTORY)
        win.history.request()
        wait(1500)
        win.history.slider.setValue(int(win.history.slider.maximum() * 0.55))
        wait(500)
        shot("history.png")
        win.history.exit_preview()
        wait(300)

    win.show_tab(win.TAB_PEOPLE)
    win.toasts.show("Asha joined", "success")
    win.toasts.show("Exported to ~/Desktop/CN-2026.png", "success")
    win.toasts.show("Reconnected. 3 missed changes were replayed.", "info")
    wait(200)
    shot("toast.png", keep_toasts=True)

    clear_toasts()
    win.open_invite()
    wait(300)
    shot("invite.png")
    win.invite.close_popover()

    win.show_shortcuts()
    wait(300)
    shot("shortcuts.png")
    win.shortcuts.hide()

    def traffic(n=10, delay=300):
        for k in range(n):
            ctl.add_object({"type": "stroke", "style": {"color": "#8e4ec6", "width": 3},
                            "geom": {"pts": [1180 + k * 26, 720, 1192 + k * 26, 740, 1204 + k * 26, 716]}})
            others[k % 3][1].send_cursor(300 + k * 30, 500)
            wait(delay)

    win.show_tab(win.TAB_NET)
    win.body.setSizes([W - 500, 500])
    wait(200)
    fit(win)
    traffic(12)
    insp = win.network.inspector
    if insp.model.rowCount():
        insp.table.selectRow(max(0, insp.model.rowCount() - 2))
    shot("network_light.png")

    ctl.set_room(locked=True)
    wait(400)
    fit(vwin)
    vwin.show_tab(vwin.TAB_PEOPLE)
    wait(500)
    vwin.rail.setFocus()
    if want("viewer_locked"):
        save(vwin.grab(), "viewer_locked.png")
    ctl.set_room(locked=False)
    wait(400)

    theme.apply_theme(app, "dark")
    wait(400)
    win.show_tab(win.TAB_CHAT)
    shot("dark.png")
    sids = find_sticky_ids(ctl)
    if sids:
        win.canvas.canvas.set_selection(sids[:2])
        wait(300)
    win.show_tab(win.TAB_PEOPLE)
    win.body.setSizes([W - 400, 400])
    wait(200)
    fit(win)
    shot("canvas_dark_selection.png")
    win.canvas.canvas.set_selection([])

    win.show_tab(win.TAB_NET)
    sa = win.network.findChild(QScrollArea)
    sa.verticalScrollBar().setValue(0)
    win.body.setSizes([W - 500, 500])
    wait(200)
    fit(win)
    traffic(14, 250)
    if insp.model.rowCount():
        insp.table.selectRow(max(0, insp.model.rowCount() - 2))
    shot("network_dark.png")

    chaos = win.network.chaos
    chaos.enable.setChecked(True)
    chaos.sliders["latency_ms"].setValue(120)
    chaos.sliders["jitter_ms"].setValue(40) if "jitter_ms" in chaos.sliders else None
    for key in chaos.sliders:
        if key.startswith("drop"):
            chaos.sliders[key].setValue(20)
    chaos.sliders["dup_pct"].setValue(15)
    traffic(16, 250)
    wait(1500)
    if insp.model.rowCount():
        insp.table.selectRow(max(0, insp.model.rowCount() - 3))
    sa.verticalScrollBar().setValue(win.network.sections[1].parentWidget().y() + 150)
    shot("network_dark_inspector.png")
    win.network.sections[1].toggle.setChecked(False)
    wait(200)
    sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
    chaos.update_counters()
    shot("chaos_lab.png")
    win.network.sections[1].toggle.setChecked(True)

    chaos.reset()
    chaos.enable.setChecked(False)
    wait(300)
    if want("offline"):
        chaos.cut()
        wait(120)
        for k in range(3):
            ctl.add_object({"type": "sticky", "style": {"fill": "#ffe0b2", "font_size": 16},
                            "geom": {"x": 330 + k * 250, "y": 780, "w": 220, "h": 110,
                                     "text": f"drawn offline #{k + 1}"}})
        win.show_tab(win.TAB_NET)
        sa.verticalScrollBar().setValue(0)
        wait(250)
        save(win.grab(), "offline.png")
        wait(4000)
        win.show_tab(win.TAB_PEOPLE)
        clear_toasts()
        wait(300)
        save(win.grab(), "reconnected.png")

    theme.apply_theme(app, "light")
    wait(300)

    if want("password_prompt"):
        pnet = NetClient()
        pctl = SessionController(pnet)
        pwin = MainWindow(pctl, settings=tmp_settings(), lan_addresses=[])
        pwin.resize(W, H)
        pwin.show()
        grabbed: dict = {}

        def grab_modal():
            dlg = QApplication.activeModalWidget()
            if dlg is None:
                QTimer.singleShot(150, grab_modal)
                return
            wait(300)
            grabbed["dlg"] = dlg.grab()
            grabbed["win"] = pwin.grab()
            dlg.reject()

        QTimer.singleShot(600, grab_modal)
        pnet.connect_to(HOST, PORT, "Guest", SECRET_ROOM)
        wait(3500)
        if "dlg" in grabbed:
            save(composite_dialog(grabbed["win"], grabbed["dlg"]), "password_prompt.png")
        pnet.close()
        pwin.hide()

    for n, _c in others:
        n.close()
    secret[0].close()
    net.close()
    wait(300)
    win.hide()
    vwin.hide()


def composite_dialog(back: QPixmap, dlg: QPixmap) -> QImage:
    """The modal dialog over its (dimmed) parent window, as the OS would show it."""
    dpr = back.devicePixelRatio()
    img = QImage(back.size(), QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor("#000000"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    lw, lh = back.width() / dpr, back.height() / dpr
    p.drawPixmap(QRectF(0, 0, lw, lh), back, QRectF(0, 0, back.width(), back.height()))
    p.fillRect(QRectF(0, 0, lw, lh), QColor(15, 18, 30, 110))
    ddpr = dlg.devicePixelRatio()
    dw, dh = dlg.width() / ddpr, dlg.height() / ddpr
    x, y = (lw - dw) / 2, (lh - dh) / 2.6
    for i in range(14, 0, -1):
        p.fillRect(QRectF(x - i, y - i + 8, dw + 2 * i, dh + 2 * i), QColor(0, 0, 0, 6))
    p.drawPixmap(QRectF(x, y, dw, dh), dlg, QRectF(0, 0, dlg.width(), dlg.height()))
    p.end()
    return img


ANSI = re.compile(r"\x1b\[(\d+(?:;\d+)*)m")
SGR = {"1": "bold", "2": "dim", "31": "#ff6b6b", "32": "#4ade80", "33": "#facc15", "36": "#22d3ee"}


def shoot_dashboard() -> None:
    CHILD_ENV.update(COLUMNS="112", LINES="40")
    env = CHILD_ENV
    proc, port, log_path = start_cli_server("--dashboard")
    try:
        wait(1500)
        clients = []
        n, c = NetClient(), None
        for i, name in enumerate(("Yashvanth", "Jeeva", "Sai Teja", "Nithilla")):
            n = NetClient()
            c = SessionController(n)
            n.connect_to("127.0.0.1", port, name, ROOM, create=(i == 0), room_name=ROOM_NAME)
            clients.append((n, c))
            wait(500)
        clients[0][1].set_role(clients[3][0].user_id, "viewer")
        clients[2][1].send_signal("hand_up")
        bots = subprocess.Popen([sys.executable, "-m", "lanboard.tools.bot", "--port", str(port),
                                 "--room", "STRESS", "--bots", "4", "--rate", "15", "--seconds", "8"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=ROOT)
        for k in range(24):
            ctl = clients[k % 3][1]
            ctl.add_object({"type": "rect", "style": {"color": "#1d4ed8", "width": 2},
                            "geom": {"x": k * 20, "y": 10, "w": 40, "h": 30}})
            wait(250)
        wait(1200)
        text = open(log_path, encoding="utf-8", errors="replace").read()
        bots.wait(20)
        for n, _c in clients:
            n.close()
    finally:
        proc.terminate()
        proc.wait(10)
    head, _, rest = text.partition("\x1b[H\x1b[2J")
    frames = [f for f in ("\x1b[H\x1b[2J" + rest).split("\x1b[H\x1b[2J") if f.strip()]
    frame = frames[-2] if len(frames) > 1 else frames[-1]
    prompt = f"$ python -m lanboard.server --port {port} --name {SERVER_NAME} --no-discovery --dashboard"
    save(render_terminal(prompt, frame), "server_dashboard.png")


def render_terminal(prompt: str, frame: str) -> QImage:
    mono = QFont(theme.MONO_FAMILY or "JetBrains Mono")
    mono.setPixelSize(15)
    fm = QFontMetricsF(mono)
    cw, lh = fm.horizontalAdvance("M"), fm.height() * 1.25
    lines = [ln.rstrip() for ln in frame.rstrip().split("\n")]
    cols = max(100, max(len(ANSI.sub("", ln)) for ln in lines) + 2)
    pad, bar = 22, 38
    w = int(pad * 2 + cols * cw)
    h = int(bar + pad * 2 + (len(lines) + 2) * lh)
    scale = 2
    img = QImage(w * scale, h * scale, QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(scale)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), 12, 12)
    p.fillPath(path, QColor("#0f1220"))
    top = QPainterPath()
    top.addRoundedRect(QRectF(0, 0, w, bar + 12), 12, 12)
    p.fillPath(top, QColor("#1c2033"))
    p.fillRect(QRectF(0, bar, w, 12), QColor("#0f1220"))
    for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        p.setBrush(QColor(col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(22 + i * 20, bar / 2), 6, 6)
    tf = QFont(mono); tf.setPixelSize(13)
    p.setFont(tf); p.setPen(QColor("#9aa3b8"))
    p.drawText(QRectF(0, 0, w, bar), Qt.AlignmentFlag.AlignCenter, "lanboard-server — dashboard")
    p.setFont(mono)
    y = bar + pad + fm.ascent()
    p.setPen(QColor("#8fb0e6"))
    p.drawText(QPointF(pad, y), prompt)
    y += lh * 1.6
    for ln in lines:
        x = pad
        style = {"bold": False, "dim": False, "color": "#e6e9f2"}
        pos = 0
        for m in list(ANSI.finditer(ln)) + [None]:
            seg = ln[pos:m.start()] if m else ln[pos:]
            if seg:
                f = QFont(mono); f.setBold(style["bold"])
                p.setFont(f)
                col = QColor(style["color"])
                if style["dim"]:
                    col = QColor("#7a8299")
                p.setPen(col)
                p.drawText(QPointF(x, y), seg)
                x += cw * len(seg)
            if m is None:
                break
            for code in m.group(1).split(";"):
                if code == "0":
                    style = {"bold": False, "dim": False, "color": "#e6e9f2"}
                elif SGR.get(code) == "bold":
                    style["bold"] = True
                elif SGR.get(code) == "dim":
                    style["dim"] = True
                elif code in SGR:
                    style["color"] = SGR[code]
            pos = m.end()
        y += lh
    p.end()
    return img


if __name__ == "__main__":
    t0 = time.time()
    main_window_shots()
    SERVER_PROC.terminate()
    if want("connect") or want("connect_host"):
        shoot_connect()
    if want("server_dashboard"):
        shoot_dashboard()
    print(f"done in {time.time() - t0:.0f}s: {len(saved)} files in {OUT}", flush=True)
    os._exit(0)
