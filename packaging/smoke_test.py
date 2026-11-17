"""Smoke test for the frozen PyInstaller bundle (dist/LANBoard), on any OS.

Usage (from the repository root, after building)::

    python packaging/smoke_test.py [--dist dist/LANBoard] [--seconds 6]

What it checks, against the *frozen* executables (not the source tree):

1. ``lanboard-server`` starts on a free port (never the default 5050) with a
   scratch ``--data`` directory and ``--no-discovery`` (so UDP 5051 is not touched),
   and prints its LAN address.
2. ``lanboard-client`` runs headless (``QT_QPA_PLATFORM=offscreen``), creates a
   room, and the server log shows that it joined. Its stderr must not contain
   Qt's "missing font family" warning (the bundled Inter / JetBrains Mono fonts
   must load).
3. ``python -m lanboard.tools.bot`` (from ``src/``) runs 4 bots against the
   frozen server and must print ``ALL REPLICAS CONVERGED``; the frozen client
   must still be alive afterwards (it received every op).
4. The bundle contains the fonts and the pdfium shared library.
5. Informational: a second server on the same (busy) port exits with an error.

Exit code 0 means every check passed. CI runs this after each build
(.github/workflows/build.yml).
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ".exe" if os.name == "nt" else ""
RESERVED_PORTS = {5050, 5051}


def free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port


def port_accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def wait_for(pred, timeout: float, step: float = 0.2) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def stop(proc: subprocess.Popen | None) -> None:
    """SIGTERM (graceful: the server snapshots its rooms), kill after 5 s."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM) if os.name != "nt" else proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default=str(ROOT / "dist" / "LANBoard"), help="bundle folder")
    ap.add_argument("--seconds", type=int, default=6, help="bot run time")
    args = ap.parse_args()

    dist = Path(args.dist).resolve()
    server_exe, client_exe = dist / f"lanboard-server{EXE}", dist / f"lanboard-client{EXE}"
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
        return ok

    if not check("executables exist", server_exe.is_file() and client_exe.is_file(), str(dist)):
        return 1

    internal = dist / "_internal"
    fonts = sorted(p.name for p in (internal / "lanboard" / "client" / "assets" / "fonts").glob("*.ttf"))
    check("bundled fonts", {"Inter-Regular.ttf", "JetBrainsMono-Regular.ttf"} <= set(fonts), ", ".join(fonts))
    pdfium = [p.name for p in (internal / "pypdfium2_raw").glob("*pdfium*")]
    check("pdfium library bundled", bool(pdfium), ", ".join(pdfium))

    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="lanboard-smoke-"))
    server_log, client_log = tmp / "server.log", tmp / "client.log"
    server = client = None
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        with open(server_log, "wb") as slog:
            server = subprocess.Popen([str(server_exe), "--port", str(port), "--data", str(tmp / "data"),
                                       "--no-discovery"], stdout=slog, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL)
        up = wait_for(lambda: port_accepts(port) or server.poll() is not None, 60)
        up = up and server.poll() is None
        slines = server_log.read_text("utf-8", "replace")
        check(f"frozen server listens on {port}", up,
              next((ln for ln in slines.splitlines() if "connect to" in ln or "No LAN" in ln), slines[-300:]))
        if not up:
            return 1

        with open(client_log, "wb") as clog:
            client = subprocess.Popen([str(client_exe), "--port", str(port), "--user", "smoke",
                                       "--room", "SMOKE", "--create"], stdout=clog, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, env=env)
        joined = wait_for(lambda: "smoke joined" in server_log.read_text("utf-8", "replace")
                          or client.poll() is not None, 60)
        line = next((ln for ln in server_log.read_text("utf-8", "replace").splitlines()
                     if "smoke joined" in ln), "")
        check("frozen client joined (server log)", joined and bool(line), line.strip()
              or f"client exit code {client.poll()}")

        env_bot = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        bot = subprocess.run([sys.executable, "-m", "lanboard.tools.bot", "--port", str(port), "--room", "SMOKE",
                              "--bots", "4", "--seconds", str(args.seconds)],
                             capture_output=True, text=True, env=env_bot, timeout=args.seconds + 90)
        tail = [ln for ln in bot.stdout.splitlines() if ln.strip()][-6:]
        check("bots converged", "ALL REPLICAS CONVERGED" in bot.stdout, " | ".join(tail) or bot.stderr[-500:])
        check("frozen client survived the op stream", client.poll() is None,
              "" if client.poll() is None else f"exit code {client.returncode}")

        dup = subprocess.run([str(server_exe), "--port", str(port), "--data", str(tmp / "data2"),
                              "--no-discovery"], capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL)
        last = (dup.stdout + dup.stderr).strip().splitlines()[-1:] or [""]
        print(f"[INFO] second server on busy port {port}: exit {dup.returncode}: {last[0]}", flush=True)
    finally:
        stop(client)
        stop(server)

    ctext = client_log.read_text("utf-8", "replace") if client_log.exists() else ""
    check("no 'missing font family' warning from the client", "missing font family" not in ctext,
          "" if os.name != "nt" else "(windowed exe on Windows: Qt warnings are not on stderr)")
    stext = server_log.read_text("utf-8", "replace")
    check("no traceback in server log", "Traceback" not in stext)
    if os.name != "nt":
        check("graceful server shutdown on SIGTERM", "server stopped" in stext)
    print(f"logs: {tmp}")
    failed = [r for r in results if not r[1]]
    print("SMOKE TEST " + ("PASSED" if not failed else f"FAILED ({len(failed)} checks)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
