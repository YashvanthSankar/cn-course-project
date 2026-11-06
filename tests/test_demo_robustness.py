"""Demo-day robustness: busy port, LAN address listing, reusing an existing server."""
import socket
import subprocess
import sys
import threading

from lanboard.client.ui.server_launcher import is_lanboard_server
from lanboard.server.discovery import lan_ipv4_addresses

from test_e2e_client import ServerThread


def test_lan_addresses_are_usable():
    ips = lan_ipv4_addresses()
    assert all(not ip.startswith(("127.", "169.254.", "0.")) for ip in ips)
    assert len(ips) == len(set(ips))
    for ip in ips:
        socket.inet_aton(ip)                       # every entry is a valid dotted quad


def test_busy_port_gives_clear_error(tmp_path):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        r = subprocess.run([sys.executable, "-m", "lanboard.server", "--host", "127.0.0.1",
                            "--port", str(port), "--data", str(tmp_path), "--no-discovery"],
                           capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "Cannot listen" in r.stderr and "Traceback" not in r.stderr


def test_is_lanboard_server_tells_real_server_from_other_program(tmp_path):
    srv = ServerThread(tmp_path / "data")
    try:
        assert is_lanboard_server(srv.port)
    finally:
        srv.stop()
    # some unrelated program that accepts and sends garbage
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]

        def serve():
            conn, _ = other.accept()
            with conn:
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        assert not is_lanboard_server(port, timeout=0.5)
        t.join(2)
