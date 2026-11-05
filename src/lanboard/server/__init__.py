"""LANBoard server: asyncio TCP sequencer for shared whiteboards.

Modules:
    server       listener, ClientConnection (reader/writer tasks), handshake, watchdog
    room         Room (board, op log, members, chat, admin) and Session
    persistence  snapshot + append-only op log per room, saves, blobs
    discovery    UDP LAN discovery responder
    dashboard    live ANSI terminal dashboard (--dashboard)
"""

from .room import Room, Session
from .server import ClientConnection, Server

__all__ = ["Server", "ClientConnection", "Room", "Session"]
