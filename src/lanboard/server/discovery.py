"""LAN discovery responder (UDP) and local address helpers.

How discovery works
-------------------
A client that does not know the server's IP sends a small UDP datagram
``{"magic": DISCOVERY_MAGIC}`` to the **broadcast address** (e.g.
``255.255.255.255`` or ``192.168.1.255``) on ``DISCOVERY_PORT``. Every host on
the subnet receives it; a LANBoard server answers by **unicast** to the
sender's address and port with its name, TCP port and room list. The client
collects the replies for a moment and shows a "Nearby servers" list.

UDP fits this job: it supports broadcast (TCP cannot), a lost probe is
simply retried, and no connection state is needed. Broadcasts do not cross
routers, which matches the LAN-only scope of the project.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from typing import TYPE_CHECKING

from lanboard.common import config as C

if TYPE_CHECKING:
    from .server import Server

log = logging.getLogger("lanboard.discovery")

MAX_PROBE = 2048            # ignore anything larger than a sane probe
MAX_ROOMS_LISTED = 40       # keep the reply in one comfortably sized datagram


def local_ip_for(remote_ip: str) -> str | None:
    """Return the local IPv4 address the OS would use to reach ``remote_ip``.

    ``connect()`` on a UDP socket sends nothing; it only makes the kernel pick a
    route and source address, which we then read back with ``getsockname``.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((remote_ip, 9))
            ip = s.getsockname()[0]
            return None if ip.startswith("0.") else ip
    except OSError:
        return None


def _interface_ipv4s() -> list[str]:
    """IPv4 address of every network interface, via the SIOCGIFADDR ioctl (POSIX).

    The route probes below only find the interface behind the default route, so a
    second network card (or an isolated switch with no gateway, i.e. internet off)
    would be missed. Windows has no ``fcntl``; there ``getaddrinfo(hostname)``
    already lists every adapter.
    """
    try:
        import fcntl
        import struct
    except ImportError:
        return []
    req = 0x8915 if sys.platform.startswith("linux") else 0xC0206921     # SIOCGIFADDR
    out: list[str] = []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return []
    for name in names:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                ifreq = fcntl.ioctl(s.fileno(), req, struct.pack("256s", name.encode()[:15]))
            except OSError:
                continue                        # interface has no IPv4 address
            out.append(socket.inet_ntoa(ifreq[20:24]))   # sockaddr_in.sin_addr
    return out


def _rank(ip: str) -> int:
    """Typical LAN ranges first; Docker bridges, VirtualBox host-only and VPN/CGNAT last."""
    if ip.startswith("192.168.56."):
        return 5                                # VirtualBox host-only adapter
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if ip.startswith(("172.17.", "172.18.")):
        return 4                                # usually Docker bridges
    if ip.startswith("172."):
        return 2
    return 6                                    # 100.x CGNAT / VPN / public


def lan_ipv4_addresses() -> list[str]:
    """Best-effort list of this machine's non-loopback IPv4 addresses, most likely LAN first."""
    found: list[str] = []
    probes = ["10.255.255.255", "192.168.255.255", "172.31.255.255", "8.8.8.8"]
    for target in probes:
        ip = local_ip_for(target)               # UDP connect(): sends no packet
        if ip and ip not in found:
            found.append(ip)
    for ip in _interface_ipv4s():
        if ip not in found:
            found.append(ip)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    found = [ip for ip in found if not ip.startswith(("127.", "169.254.", "0."))]
    return sorted(found, key=lambda ip: (_rank(ip), found.index(ip)))


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Answers discovery probes with a unicast description of this server."""

    def __init__(self, server: "Server") -> None:
        self.server = server
        self.transport: asyncio.DatagramTransport | None = None
        self.probes = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport                      # type: ignore[assignment]

    def reply_for(self, addr: tuple[str, int]) -> bytes:
        srv = self.server
        info = {"magic": C.DISCOVERY_MAGIC, "server_id": srv.server_id, "name": srv.name,
                "port": srv.port, "proto": C.PROTO_VERSION, "tls": srv.ssl_context is not None,
                "rooms": srv.room_summaries()[:MAX_ROOMS_LISTED]}
        host = srv.host if srv.host not in ("", "0.0.0.0", "::") else local_ip_for(addr[0])
        if host:
            info["host"] = host
        return json.dumps(info, separators=(",", ":")).encode("utf-8")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > MAX_PROBE or self.transport is None:
            return
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict) or msg.get("magic") != C.DISCOVERY_MAGIC:
            return                                       # not for us: stay silent
        self.probes += 1
        try:
            self.transport.sendto(self.reply_for(addr), addr)
        except OSError as exc:
            log.debug("discovery reply to %s failed: %s", addr, exc)

    def error_received(self, exc: Exception) -> None:   # ICMP errors etc.; harmless
        log.debug("discovery socket error: %s", exc)


async def start_discovery(server: "Server", port: int) -> asyncio.DatagramTransport | None:
    """Bind the UDP responder on all interfaces; warn and carry on if the port is busy."""
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: DiscoveryProtocol(server), local_addr=("0.0.0.0", port),
            allow_broadcast=True)
    except OSError as exc:
        log.warning("LAN discovery disabled: cannot bind UDP port %d (%s)", port, exc)
        return None
    log.info("LAN discovery listening on UDP %d", transport.get_extra_info("sockname")[1])
    return transport
