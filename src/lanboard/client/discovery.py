"""LAN server discovery over UDP broadcast.

TCP needs to know the server's IP address, but users should not have to type
it. Discovery uses a connectionless UDP *broadcast*:

1. The client sends a small datagram ``{"magic": DISCOVERY_MAGIC}`` to UDP
   port ``DISCOVERY_PORT`` at a broadcast address. A broadcast datagram is
   delivered to every host on the subnet, and only hosts with a socket bound
   to that port (our servers) read it.
2. Each server replies with a **unicast** datagram straight to the source
   address and port of the probe::

       {magic, server_id, name, port, proto, rooms:[{code, name, users, protected, locked}]}

3. The server's IP is not in the reply: we take it from the datagram's
   *sender address*, which the IP header always carries. This is the address
   the client can actually reach, even on a multi-homed server.

Broadcast addresses: the "limited broadcast" ``255.255.255.255`` never
crosses a router, but on hosts with several interfaces (Wi-Fi + Ethernet,
VPN, virtual adapters) the OS sends it out of only one of them. We therefore
also send to each interface's *directed* broadcast address (for example
``192.168.1.255`` for ``192.168.1.0/24``) from ``QNetworkInterface``, plus
``127.0.0.1`` so that a server on the same machine is always found.

UDP is unreliable, so a probe or a reply may be lost. That is harmless: we
probe every 2 s and forget a server only when it has not answered for 6 s
(three missed probes). Replies are de-duplicated by ``server_id``, because one
server answers each of the several probes.
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QNetworkInterface, QUdpSocket

from ..common.config import DISCOVERY_MAGIC, DISCOVERY_PORT

PROBE_INTERVAL_MS = 2000
ENTRY_TTL = 6.0


class DiscoveryBrowser(QObject):
    """Finds LANBoard servers on the local network.

    ``servers_changed(list)`` carries entries
    ``{server_id, name, host, port, proto, rooms, last_seen}``, sorted by name.
    It is emitted when a server appears, disappears or changes. A reply that
    only refreshes ``last_seen`` does not emit.
    """

    servers_changed = Signal(list)

    def __init__(self, parent: QObject | None = None, *, port: int = DISCOVERY_PORT,
                 interval_ms: int = PROBE_INTERVAL_MS, ttl: float = ENTRY_TTL) -> None:
        super().__init__(parent)
        self.port = port
        self.ttl = ttl
        self._servers: dict[str, dict] = {}
        self._sock: QUdpSocket | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    # ---------------------------------------------------------------- API
    def start(self) -> None:
        """Bind a UDP socket and probe now, then every ``interval_ms``."""
        if self._sock is None:
            sock = QUdpSocket(self)
            # Port 0 = any free ephemeral port. Replies come back to it as unicast.
            if not sock.bind(QHostAddress(QHostAddress.SpecialAddress.AnyIPv4), 0):
                sock.deleteLater()
                return
            sock.readyRead.connect(self._on_ready_read)
            self._sock = sock
        self._timer.start()
        self.probe()

    def stop(self) -> None:
        self._timer.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock.deleteLater()
            self._sock = None

    def servers(self) -> list[dict]:
        return sorted((dict(s) for s in self._servers.values()),
                      key=lambda s: (str(s.get("name", "")).lower(), s["server_id"]))

    def probe(self) -> None:
        """Send one discovery datagram to every broadcast target."""
        if self._sock is None:
            return
        datagram = json.dumps({"magic": DISCOVERY_MAGIC}).encode()
        for addr in self._targets():
            # Errors such as "network unreachable" on a down interface are
            # expected and ignored. Other targets still work.
            self._sock.writeDatagram(datagram, QHostAddress(addr), self.port)

    # ---------------------------------------------------------- internals
    @staticmethod
    def _targets() -> list[str]:
        targets = ["255.255.255.255", "127.0.0.1"]
        flags = QNetworkInterface.InterfaceFlag
        for iface in QNetworkInterface.allInterfaces():
            f = iface.flags()
            if not (f & flags.IsUp and f & flags.IsRunning and f & flags.CanBroadcast):
                continue
            if f & flags.IsLoopBack:
                continue
            for entry in iface.addressEntries():
                bcast = entry.broadcast()
                if (not bcast.isNull()
                        and bcast.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol):
                    s = bcast.toString()
                    if s not in targets:
                        targets.append(s)
        return targets

    def _tick(self) -> None:
        self._expire()
        self.probe()

    def _expire(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._servers.items() if now - s["last_seen"] > self.ttl]
        if stale:
            for sid in stale:
                del self._servers[sid]
            self.servers_changed.emit(self.servers())

    def _on_ready_read(self) -> None:
        sock = self._sock
        if sock is None:
            return
        changed = False
        while sock.hasPendingDatagrams():
            dg = sock.receiveDatagram()
            entry = self._parse(bytes(dg.data().data()), dg.senderAddress())
            if entry is not None:
                changed |= self._upsert(entry)
        if changed:
            self.servers_changed.emit(self.servers())
        self._expire()

    @staticmethod
    def _parse(data: bytes, sender: QHostAddress) -> dict | None:
        """Validate one reply. Our own probes and foreign traffic return None."""
        try:
            msg = json.loads(data)
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(msg, dict) or msg.get("magic") != DISCOVERY_MAGIC:
            return None
        sid, port = msg.get("server_id"), msg.get("port")
        if not isinstance(sid, str) or not sid or not isinstance(port, int) or isinstance(port, bool):
            return None              # a probe (no server_id) or a malformed reply
        host = sender.toString()
        if sender.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
            v4 = sender.toIPv4Address()        # IPv4-mapped IPv6 (::ffff:a.b.c.d) -> a.b.c.d
            if v4:
                host = QHostAddress(v4).toString()
        if not host:
            return None
        rooms = msg.get("rooms")
        return {
            "server_id": sid,
            "name": str(msg.get("name") or host),
            "host": host,
            "port": port,
            "proto": msg.get("proto"),
            "rooms": [r for r in rooms if isinstance(r, dict)] if isinstance(rooms, list) else [],
            "last_seen": time.time(),
        }

    def _upsert(self, entry: dict) -> bool:
        """Insert or refresh a server. Returns True if anything visible changed."""
        old = self._servers.get(entry["server_id"])
        if old is not None and entry["host"].startswith("127.") and not old["host"].startswith("127."):
            # The same server answered both a broadcast and our loopback
            # probe. Keep the LAN address, which other machines can reach too.
            entry["host"] = old["host"]
        self._servers[entry["server_id"]] = entry
        if old is None:
            return True
        return any(old.get(k) != entry.get(k) for k in ("name", "host", "port", "proto", "rooms"))
