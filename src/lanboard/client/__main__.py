"""LANBoard desktop client entry point: ``python -m lanboard.client`` / ``lanboard-client``.

Usage::

    lanboard-client                                   # welcome screen
    lanboard-client --room K7Q-2MX --user alice       # join directly (host defaults to 127.0.0.1)
    lanboard-client --host 192.168.1.20 --port 5050 --user bob --room K7Q-2MX
    lanboard-client --create --room DEMO --name "CN Lab" --password secret --user owner
    lanboard-client --theme dark

Flow: create the QApplication, register the bundled fonts, apply the theme, show the :class:`ConnectDialog`
(skipped when ``--room`` is given), then build ``NetClient`` →
``SessionController(net)`` → ``MainWindow(controller)`` and call
``net.connect_to(...)``. When a session ends (Leave, kicked, room not found...)
the connect dialog comes back, pre-filled; closing the main window quits.
Servers started from the dialog ("Start a server on this computer") are
terminated when the app quits.
"""

from __future__ import annotations

import argparse
import getpass
import signal
import sys
from dataclasses import replace

from PySide6.QtCore import QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog

from lanboard.common import config as C

from .ui import theme
from .ui.connect_dialog import ConnectDialog, ConnectParams
from .ui.server_launcher import terminate_all


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="lanboard-client", description="LANBoard collaborative whiteboard client")
    ap.add_argument("--host", help="server IP or hostname (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=None, help=f"server TCP port (default {C.TCP_PORT})")
    ap.add_argument("--user", help="your display name")
    ap.add_argument("--room", help="room code; giving it skips the connect dialog")
    ap.add_argument("--create", action="store_true", help="create the room instead of joining it")
    ap.add_argument("--password", help="room password (create: set it; join: use it)")
    ap.add_argument("--name", help="room display name (with --create)")
    ap.add_argument("--theme", choices=("light", "dark"), help="colour theme")
    return ap.parse_args(argv)


class ClientShell(QObject):
    """Owns the current session objects and switches between dialog and window."""

    def __init__(self, app: QApplication, settings: QSettings) -> None:
        super().__init__()
        self.app = app
        self.settings = settings
        self.net = None
        self.controller = None
        self.window = None
        self.last: ConnectParams | None = None
        self.servers: list = []           # ServerProcess objects we started (kept alive)
        self.client_id: str | None = None  # NetClient.client_id reused by every session

    def show_connect(self, error_text: str = "", prefill: ConnectParams | None = None) -> None:
        dlg = ConnectDialog(prefill=prefill, error_text=error_text, settings=self.settings)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        params = dlg.params()
        dlg.deleteLater()
        if accepted and params is not None:
            self.start_session(params)
        elif self.window is None:
            self.app.quit()

    def start_session(self, p: ConnectParams) -> None:
        # imported here so the dialog appears fast and import errors show up clearly
        from lanboard.client.controller import SessionController
        from lanboard.client.net import NetClient

        from .ui.main_window import MainWindow

        if p.server is not None and p.server not in self.servers:
            self.servers.append(p.server)
        self.last = p
        self.net = NetClient()
        # One client id for the whole app run (not per session): the server bans a
        # kicked client id from the room, which must survive "Back to start" + Join.
        if self.client_id:
            self.net.client_id = self.client_id
        self.client_id = self.net.client_id
        self.controller = SessionController(self.net)
        self.window = MainWindow(self.controller, settings=self.settings, lan_addresses=p.lan_addresses)
        self.window.session_ended.connect(self.on_session_ended)
        self.window.show()
        self.net.connect_to(p.host, p.port, p.username, p.room, create=p.create,
                            password=p.password, room_name=p.room_name)

    def on_session_ended(self, reason: str, message: str) -> None:
        win, ctl, net = self.window, self.controller, self.net
        self.window = self.controller = self.net = None
        if net is not None:
            net.close()                       # stop timers/sockets before teardown
        # Keep Python references until Qt has processed deleteLater(). If the
        # wrappers were freed right here (inside a signal from the window) PySide
        # would destroy the C++ objects mid-emission, which can segfault.
        self._retired = [o for o in (win, ctl, net) if o is not None]
        if win is not None:
            win.hide()
            win.deleteLater()
        for obj in (ctl, net):
            if obj is not None:
                obj.deleteLater()
        if reason == "quit":
            self.app.quit()
            return
        prefill = None
        if self.last is not None:
            # the room exists now (we created it), so come back in "join" mode
            prefill = replace(self.last, create=False, start_server=False, server=None, lan_addresses=[])
        QTimer.singleShot(0, lambda: self.show_connect(message if reason == "fatal" else "", prefill))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("LANBoard")
    app.setApplicationDisplayName("LANBoard")
    app.setOrganizationName("LANBoard")
    app.setOrganizationDomain("lanboard.local")
    app.setQuitOnLastWindowClosed(False)            # the shell decides when to quit
    settings = QSettings()
    theme.load_fonts()                              # bundled Inter / JetBrains Mono (falls back to system fonts)
    theme.apply_theme(app, args.theme or str(settings.value("ui/theme", "light")))
    app.setWindowIcon(theme.app_icon())
    app.aboutToQuit.connect(terminate_all)

    # Ctrl+C in the terminal: quit cleanly (so child servers are terminated too)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.start(250)                                 # let Python run its signal handlers
    wake.timeout.connect(lambda: None)

    shell = ClientShell(app, settings)
    if args.room:
        user = args.user or str(settings.value("connect/username", "") or "") or getpass.getuser()
        params = ConnectParams(host=args.host or "127.0.0.1", port=args.port or C.TCP_PORT,
                               username=user[: C.MAX_USERNAME], room=args.room, create=args.create,
                               password=args.password, room_name=args.name)
        QTimer.singleShot(0, lambda: shell.start_session(params))
    else:
        prefill = None
        if args.host or args.user or args.port:
            prefill = ConnectParams(host=args.host or "127.0.0.1", port=args.port or C.TCP_PORT,
                                    username=args.user or "", create=args.create, room_name=args.name)
        QTimer.singleShot(0, lambda: shell.show_connect("", prefill))
    rc = app.exec()
    terminate_all()
    return rc


if __name__ == "__main__":
    sys.exit(main())
