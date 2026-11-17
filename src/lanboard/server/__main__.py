"""Command line entry point: ``python -m lanboard.server`` / ``lanboard-server``.

Usage::

    python -m lanboard.server [--host 0.0.0.0] [--port 5050] [--data DIR]
                              [--name NAME] [--no-discovery] [--dashboard]
                              [--tls-cert cert.pem --tls-key key.pem] [-v]

Rooms are stored in ``--data``; by default that is the per-user app-data
folder (``~/Library/Application Support/LANBoard`` on macOS,
``%APPDATA%\\LANBoard`` on Windows, ``~/.local/share/lanboard`` on Linux), so
it does not matter which directory the server is started from.

Ctrl+C (SIGINT) or SIGTERM shuts down gracefully: every room's board is
snapshotted to disk first. A hard kill is also safe, because accepted ops are
in ``ops.jsonl`` and are replayed on the next start (see ``persistence.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import ssl
import sys

from lanboard.common import config as C

from .dashboard import Dashboard, RingLogHandler
from .discovery import lan_ipv4_addresses
from .persistence import default_data_dir
from .server import Server

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="lanboard-server",
                                 description="LANBoard collaborative whiteboard server (LAN only)")
    ap.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    ap.add_argument("--port", type=int, default=C.TCP_PORT, help=f"TCP port (default {C.TCP_PORT})")
    ap.add_argument("--data", default=None, metavar="DIR",
                    help=f"data directory (default: {default_data_dir()})")
    ap.add_argument("--name", default=None, help="server name shown to clients (default: hostname)")
    ap.add_argument("--no-discovery", action="store_true",
                    help=f"do not answer UDP discovery probes on port {C.DISCOVERY_PORT}")
    ap.add_argument("--dashboard", action="store_true", help="live terminal dashboard")
    ap.add_argument("--tls-cert", metavar="FILE", help="PEM certificate (enables TLS)")
    ap.add_argument("--tls-key", metavar="FILE", help="PEM private key")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = ap.parse_args(argv)
    if bool(args.tls_cert) != bool(args.tls_key):
        ap.error("--tls-cert and --tls-key must be given together")
    return args


def make_ssl_context(cert: str, key: str) -> ssl.SSLContext:
    """Server-side TLS context (TLS >= 1.2; a self-signed certificate is fine).

    Only ``tools/bot.py`` can connect over TLS (it takes an ``ssl.SSLContext``,
    so the caller chooses how to verify the certificate). The desktop client
    uses plain TCP and cannot join a TLS-enabled server.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def setup_logging(verbose: bool, dashboard: bool) -> RingLogHandler | None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if dashboard:
        ring = RingLogHandler(size=10)
        ring.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        root.addHandler(ring)
        return ring
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    return None


async def run(args: argparse.Namespace) -> None:
    ring = setup_logging(args.verbose, args.dashboard)
    ctx = make_ssl_context(args.tls_cert, args.tls_key) if args.tls_cert else None
    server = Server(args.host, args.port, args.data, args.name,
                    discovery=not args.no_discovery, ssl_context=ctx)
    try:
        await server.start()
    except OSError as exc:
        print(f"Cannot listen on {args.host}:{args.port}: {exc.strerror or exc}.\n"
              f"Is another LANBoard server already running? "
              f"Try a different port, e.g.  lanboard-server --port {args.port + 10}",
              file=sys.stderr, flush=True)
        raise SystemExit(2) from None

    wildcard = args.host in ("", "0.0.0.0", "::")
    addrs = lan_ipv4_addresses() if wildcard else [args.host]
    print(f"LANBoard server {server.name!r} listening on {args.host}:{server.port}"
          f"{' (TLS)' if ctx else ''}", flush=True)
    print(f"Data directory: {server.storage.root}", flush=True)
    if addrs:
        print("Clients on this LAN can connect to: "
              + ", ".join(f"{ip}:{server.port}" for ip in addrs), flush=True)
    else:
        print("No LAN IPv4 address found; is this machine connected to a network?", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    dash_task = asyncio.create_task(Dashboard(server, ring).run()) if args.dashboard else None
    try:
        await stop.wait()
    finally:
        if dash_task is not None:
            dash_task.cancel()
        logging.getLogger("lanboard.server").info("shutting down, saving rooms...")
        await server.stop(flush=True)


def _owns_console() -> bool:
    """True when this is a frozen Windows exe started by double-click (its own console).

    Then the console window closes the moment we exit, so an error message would
    flash by unread. A server started by the client (stdin = DEVNULL) or from a
    terminal does not own its console and must never block.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes
        buf = (ctypes.c_uint * 2)()
        return ctypes.windll.kernel32.GetConsoleProcessList(buf, 2) <= 1 and bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        if exc.code not in (0, None) and _owns_console():
            input("Press Enter to close...")
        raise


if __name__ == "__main__":
    main()
