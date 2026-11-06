"""Start a LANBoard server on this computer from the client ("host" mode).

The server is a separate process, exactly as if it were started from a terminal,
so the client stays a pure client and the server keeps the same concurrency
model. The command is:

* from source / pip install: ``[sys.executable, "-m", "lanboard.server", "--port", P]``
* in a frozen build (PyInstaller): the sibling executable ``lanboard-server[.exe]``
  next to ``sys.executable``.

:class:`ServerProcess` polls the TCP port with a short QTimer until it accepts
connections (or the process dies / a timeout passes) and emits ``ready`` or
``failed``. :func:`lan_ipv4_addresses` lists this machine's LAN IPv4 addresses
(through ``QNetworkInterface``) so the host can tell others what to type.
Every started process is registered and terminated when the app quits.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

_RUNNING: list["ServerProcess"] = []


_VIRTUAL_HINTS = ("virtualbox", "vmware", "vethernet", "hyper-v", "wsl", "docker", "utun",
                  "tailscale", "zerotier", "vboxnet", "bridge", "vmnet")


def _is_virtual(iface: QNetworkInterface) -> bool:
    """VM / container / VPN adapters: real but useless as an address for classmates."""
    if iface.type() == QNetworkInterface.InterfaceType.Virtual:
        return True
    names = (iface.name() + " " + iface.humanReadableName()).lower()
    return any(h in names for h in _VIRTUAL_HINTS)


def lan_ipv4_addresses() -> list[str]:
    """Non-loopback, non-link-local IPv4 addresses of interfaces that are up.

    Physical adapters come first, so ``[0]`` is what the Invite popover shows.
    """
    out: list[str] = []
    virtual: set[str] = set()
    for iface in QNetworkInterface.allInterfaces():
        flags = iface.flags()
        if not (flags & QNetworkInterface.InterfaceFlag.IsUp) or not (flags & QNetworkInterface.InterfaceFlag.IsRunning):
            continue
        if flags & QNetworkInterface.InterfaceFlag.IsLoopBack:
            continue
        for entry in iface.addressEntries():
            ip = entry.ip()
            if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            text = ip.toString()
            if text.startswith("169.254.") or text.startswith("127.") or text in out:
                continue
            out.append(text)
            if _is_virtual(iface):
                virtual.add(text)
    # physical before virtual, then private ranges (typical LANs) first
    def rank(a: str) -> int:
        if a in virtual or a.startswith("192.168.56."):    # .56 = VirtualBox host-only
            return 5
        if a.startswith("192.168."):
            return 0
        if a.startswith("10."):
            return 1
        if a.startswith("172.17.") or a.startswith("172.18."):
            return 3                                  # usually Docker bridges
        if a.startswith("172."):
            return 2
        return 4                                      # CGNAT/VPN/public last
    out.sort(key=lambda a: (rank(a), a))
    return out


def port_accepts(port: int, host: str = "127.0.0.1", timeout: float = 0.15) -> bool:
    """True if something accepts TCP connections on ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_lanboard_server(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """True if ``host:port`` answers our protocol: send PING (allowed before HELLO), expect PONG.

    Used before reusing an already-listening port, so that some unrelated program
    on 5050 is reported clearly instead of producing a confusing protocol error.
    """
    from lanboard.common.messages import MsgType
    from lanboard.common.protocol import FrameDecoder, ProtocolError, encode
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(encode(MsgType.PING, {"t": 0}))
            dec = FrameDecoder()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                data = s.recv(4096)
                if not data:
                    return False
                for msg_type, _ in dec.feed(data):
                    return msg_type == MsgType.PONG
    except (OSError, ProtocolError, ValueError):
        return False
    return False


def server_command(port: int, data_dir: str | None = None) -> list[str]:
    """The argv used to start a server on ``port``."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).with_name("lanboard-server" + (".exe" if os.name == "nt" else ""))
        cmd = [str(exe)]
    else:
        cmd = [sys.executable, "-m", "lanboard.server"]
    cmd += ["--port", str(int(port))]
    if data_dir:
        cmd += ["--data", data_dir]
    return cmd


def _default_data_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) \
        or str(Path.home() / ".lanboard")
    path = Path(base) / "server-data"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    return str(path)


class ServerProcess(QObject):
    """Owns one child server process. Emits ``ready`` once the port accepts."""

    ready = Signal()
    failed = Signal(str)

    POLL_MS = 150
    TIMEOUT_S = 12.0

    def __init__(self, port: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port = int(port)
        self.proc: subprocess.Popen | None = None
        self.reused = False              # a server was already listening on the port
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.command: list[str] = []
        self.log_path: Path | None = None
        self._log = None

    def start(self) -> None:
        if port_accepts(self.port):
            if is_lanboard_server(self.port):
                # An earlier LANBoard server already serves this port: use it.
                self.reused = True
                QTimer.singleShot(0, self.ready.emit)
            else:
                QTimer.singleShot(0, lambda: self.failed.emit(
                    f"Port {self.port} is used by another program. Pick a different port."))
            return
        data_dir = _default_data_dir()
        self.command = server_command(self.port, data_dir or None)
        env = dict(os.environ)
        if not getattr(sys, "frozen", False):
            # make "-m lanboard.server" importable even when running from a source checkout
            src = str(Path(__file__).resolve().parents[3])
            env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # Logs go to a file, never to a pipe nobody drains (a full pipe would block the server).
        self.log_path = Path(data_dir or ".") / f"server-{self.port}.log"
        try:
            self._log = open(self.log_path, "wb")
            self.proc = subprocess.Popen(self.command, env=env, stdout=self._log,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
        except OSError as exc:
            QTimer.singleShot(0, lambda: self.failed.emit(f"Could not start the server: {exc}"))
            return
        _RUNNING.append(self)
        self._t0 = time.monotonic()
        self._timer.start()

    def _poll(self) -> None:
        if self.proc is not None and self.proc.poll() is not None:
            self._timer.stop()
            err = b""
            try:
                err = self.log_path.read_bytes()[-2000:]
            except OSError:
                pass
            tail = err.decode(errors="replace").strip().splitlines()[-3:]
            self.failed.emit("The server exited immediately"
                             + (": " + " ".join(tail) if tail else f" (code {self.proc.returncode})."))
            return
        if port_accepts(self.port):
            self._timer.stop()
            self.ready.emit()
        elif time.monotonic() - self._t0 > self.TIMEOUT_S:
            self._timer.stop()
            self.terminate()
            self.failed.emit(f"The server did not open port {self.port} within {self.TIMEOUT_S:.0f} s.")

    def is_running(self) -> bool:
        return self.reused or (self.proc is not None and self.proc.poll() is None)

    def terminate(self) -> None:
        """Stop the child (SIGTERM lets it snapshot boards), kill if it hangs."""
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass
        if self in _RUNNING:
            _RUNNING.remove(self)
        if self._log is not None:
            self._log.close()
            self._log = None


def terminate_all() -> None:
    """Stop every server this client started (called on app quit and at exit)."""
    for sp in list(_RUNNING):
        sp.terminate()
    _RUNNING.clear()


atexit.register(terminate_all)
