"""Assemble the course submission zip: release/LANBoard_CN_Project_2026.zip.

Usage (from anywhere; paths are relative to the repository)::

    python packaging/make_release.py [--no-local-dist] [--out release]

Layout of the zip (entries at the zip root)::

    README_SUBMISSION.txt                  team, contents, quick run steps (Windows/macOS/Linux)
    executables/<platform>/LANBoard/...    lanboard-server[.exe], lanboard-client[.exe], _internal/
    source/...                             `git archive HEAD`: committed files only
    docs/LANBoard_Technical_Documentation.pdf
    docs/LANBoard_Presentation.pptx
    video/README.txt                       drop the member explanation videos here

Inputs:

* ``dist/LANBoard`` of *this* machine (build it first with
  ``pyinstaller --noconfirm packaging/lanboard.spec``) goes to
  ``executables/<this platform>/LANBoard``, e.g. ``macos-arm64``.
* Bundles from other machines / CI go in ``release/extra/<platform>/`` (the
  folder name becomes the platform name in the zip), either as an unpacked
  ``LANBoard/`` folder or as the ``LANBoard-<OS>.zip`` produced by
  .github/workflows/build.yml (the downloaded GitHub artifact, a zip that holds
  that zip, works too).
* The two documents are looked up in ``docs/``, the repository root and
  ``release/docs/``; missing ones only produce a warning.

Unix permission bits and symlinks are stored in the zip (the macOS bundle has
symlinks into the Qt frameworks), so macOS Archive Utility / ``unzip`` restore
working executables. Uncommitted changes are *not* in ``source/``; the script
warns about them.
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_NAME = "LANBoard_CN_Project_2026.zip"
DOCS = {
    "LANBoard_Technical_Documentation.pdf": "Technical Documentation (PDF)",
    "LANBoard_Presentation.pptx": "Presentation (PPT)",
}
WIN_PATH_WARN = 150      # longest in-zip path; Windows Explorer fails beyond ~260 incl. the target folder

warnings: list[str] = []


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"WARNING: {msg}", file=sys.stderr, flush=True)


def platform_tag() -> str:
    """windows-x64, macos-arm64, linux-x64, ... for the machine running the script."""
    osname = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux")
                                                          else sys.platform)
    m = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m or "unknown")
    return f"{osname}-{arch}"


# --------------------------------------------------------------------------- zip writer
class Packer:
    """Zip writer that keeps Unix modes and symlinks and tallies bytes per section."""

    def __init__(self, path: Path) -> None:
        self.zf = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
        self.sizes: dict[str, int] = {}
        self.counts: dict[str, int] = {}
        self.longest = ""
        self.names: set[str] = set()

    def _account(self, arcname: str, size: int) -> None:
        if arcname in self.names:
            raise SystemExit(f"duplicate zip entry: {arcname}")
        self.names.add(arcname)
        section = "/".join(arcname.split("/")[:2]) if arcname.startswith("executables/") \
            else arcname.split("/")[0]
        self.sizes[section] = self.sizes.get(section, 0) + size
        self.counts[section] = self.counts.get(section, 0) + 1
        if len(arcname) > len(self.longest):
            self.longest = arcname

    def add_bytes(self, arcname: str, data: bytes, mode: int = 0o644,
                  date_time: tuple | None = None, compress: bool = True) -> None:
        zi = zipfile.ZipInfo(arcname, date_time or time.localtime()[:6])
        zi.create_system = 3                                  # Unix: honour external_attr modes
        zi.external_attr = (stat.S_IFREG | (mode & 0o7777)) << 16
        zi.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        self.zf.writestr(zi, data)
        self._account(arcname, len(data))

    def add_symlink(self, arcname: str, target: str, date_time: tuple | None = None) -> None:
        zi = zipfile.ZipInfo(arcname, date_time or time.localtime()[:6])
        zi.create_system = 3
        zi.external_attr = (stat.S_IFLNK | 0o755) << 16
        zi.compress_type = zipfile.ZIP_STORED
        self.zf.writestr(zi, target)
        self._account(arcname, 0)

    def add_dir_entry(self, arcname: str) -> None:
        zi = zipfile.ZipInfo(arcname.rstrip("/") + "/", time.localtime()[:6])
        zi.create_system = 3
        zi.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
        self.zf.writestr(zi, b"")

    def add_tree(self, src: Path, prefix: str) -> None:
        """Add a folder recursively; symlinks (to files or folders) are stored as symlinks."""
        for dirpath, dirnames, filenames in os.walk(src):
            d = Path(dirpath)
            rel = d.relative_to(src).as_posix()
            base = prefix if rel == "." else f"{prefix}/{rel}"
            for name in sorted(dirnames):
                p = d / name
                if p.is_symlink():
                    self.add_symlink(f"{base}/{name}", os.readlink(p))
            dirnames[:] = sorted(n for n in dirnames if not (d / n).is_symlink())
            for name in sorted(filenames):
                p = d / name
                arc = f"{base}/{name}"
                if p.is_symlink():
                    self.add_symlink(arc, os.readlink(p))
                    continue
                st = p.stat()
                mode = stat.S_IMODE(st.st_mode)
                if os.name == "nt" and name.lower().endswith(".exe"):
                    mode |= 0o755
                self.add_bytes(arc, p.read_bytes(), mode, time.localtime(st.st_mtime)[:6])

    def copy_zip_tree(self, zf: zipfile.ZipFile, src_prefix: str, prefix: str) -> int:
        """Copy members under ``src_prefix`` of another zip, keeping their attributes."""
        n = 0
        for info in zf.infolist():
            if not info.filename.startswith(src_prefix) or info.is_dir():
                continue
            arc = prefix + "/" + info.filename[len(src_prefix):]
            data = zf.read(info)
            zi = zipfile.ZipInfo(arc, info.date_time)
            zi.create_system, zi.external_attr = info.create_system, info.external_attr
            is_link = info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16)
            zi.compress_type = zipfile.ZIP_STORED if is_link else zipfile.ZIP_DEFLATED
            self.zf.writestr(zi, data)
            self._account(arc, len(data))
            n += 1
        return n

    def close(self) -> None:
        self.zf.close()


# --------------------------------------------------------------------------- inputs
def bundle_ok(folder: Path) -> bool:
    names = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
    return bool({"lanboard-server", "lanboard-server.exe"} & names) and \
        bool({"lanboard-client", "lanboard-client.exe"} & names)


def find_lanboard_prefix(zf: zipfile.ZipFile) -> str | None:
    """'.../LANBoard/' prefix of a bundle zip, if it contains the executables."""
    for name in zf.namelist():
        m = re.match(r"^(.*?LANBoard/)lanboard-(server|client)(\.exe)?$", name)
        if m:
            return m.group(1)
    return None


def add_extra_platform(packer: Packer, folder: Path) -> bool:
    plat = folder.name
    dest = f"executables/{plat}/LANBoard"
    if bundle_ok(folder / "LANBoard"):
        packer.add_tree(folder / "LANBoard", dest)
        print(f"  + {dest}  (from {folder / 'LANBoard'})")
        return True
    if bundle_ok(folder):
        packer.add_tree(folder, dest)
        print(f"  + {dest}  (from {folder})")
        return True
    for z in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            prefix = find_lanboard_prefix(zf)
            if prefix is None:                       # GitHub artifact: a zip that holds LANBoard-<OS>.zip
                inner = [n for n in zf.namelist() if n.lower().endswith(".zip")]
                if len(inner) == 1:
                    with zipfile.ZipFile(io.BytesIO(zf.read(inner[0]))) as zin:
                        p2 = find_lanboard_prefix(zin)
                        if p2 is not None:
                            n = packer.copy_zip_tree(zin, p2, dest)
                            print(f"  + {dest}  ({n} files from {z.name}!{inner[0]})")
                            return True
                continue
            n = packer.copy_zip_tree(zf, prefix, dest)
            print(f"  + {dest}  ({n} files from {z.name})")
            return True
    warn(f"release/extra/{plat}: no LANBoard bundle found (expected LANBoard/ or a LANBoard-*.zip)")
    return False


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def add_source(packer: Packer) -> str:
    """``git archive HEAD`` into source/; returns the commit description."""
    head = git("rev-parse", "--short", "HEAD").decode().strip()
    subject = git("log", "-1", "--format=%s").decode().strip()
    dirty = [ln for ln in git("status", "--porcelain").decode().splitlines() if ln.strip()]
    if dirty:
        warn(f"{len(dirty)} uncommitted/untracked path(s) are NOT in source/ (only HEAD {head} is): "
             + ", ".join(ln[3:] for ln in dirty[:8]) + (" ..." if len(dirty) > 8 else ""))
    tar_bytes = git("archive", "--format=tar", "HEAD")
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        for m in tf.getmembers():
            arc = f"source/{m.name}"
            if m.isfile():
                packer.add_bytes(arc, tf.extractfile(m).read(), m.mode, time.localtime(m.mtime)[:6])
            elif m.issym():
                packer.add_symlink(arc, m.linkname, time.localtime(m.mtime)[:6])
    return f"{head} ({subject})"


def find_doc(name: str, out_dir: Path) -> Path | None:
    for d in (ROOT / "docs", ROOT, out_dir / "docs"):
        if (d / name).is_file():
            return d / name
    return None


# --------------------------------------------------------------------------- texts
def team_rows() -> list[tuple[str, str, str]]:
    """The team table from README.md (``| Name | Roll no. | Area |``)."""
    rows = []
    text = (ROOT / "README.md").read_text("utf-8")
    sect = text.split("## Team", 1)[1].split("\n## ", 1)[0] if "## Team" in text else ""
    for line in sect.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Name", "") and not set(cells[0]) <= set("-: "):
            rows.append((cells[0], cells[1], cells[2]))
    return rows


def readme_text(platforms: list[str], docs_found: dict[str, bool], commit: str) -> str:
    team = team_rows()
    w = max([len(n) for n, _, _ in team] + [4])
    team_txt = "\n".join(f"  {n.ljust(w)}  {r:<10}  {a}" for n, r, a in team) or "  (see source/README.md)"
    exe_lines = "\n".join(f"  executables/{p}/LANBoard/" for p in platforms) or "  (none)"
    win = next((p for p in platforms if p.startswith("windows")), "windows-x64")
    mac = next((p for p in platforms if p.startswith("macos")), "macos-arm64")
    lin = next((p for p in platforms if p.startswith("linux")), "linux-x64")
    doc_lines = "\n".join(f"  docs/{n:<40} {desc}{'' if docs_found[n] else '   [NOT INCLUDED IN THIS BUILD]'}"
                          for n, desc in DOCS.items())
    return f"""LANBoard - LAN-only real-time collaborative whiteboard
Computer Networks Lab Project 2026
======================================================================

TEAM
  {'Name'.ljust(w)}  {'Roll no.':<10}  Area
{team_txt}

CONTENTS
{exe_lines}
      lanboard-server[.exe]   the server (console program, no GUI)
      lanboard-client[.exe]   the whiteboard client (GUI)
      _internal/              shared runtime: keep it next to the two programs
  source/                     commented source code (git commit {commit.split()[0]})
{doc_lines}
  video/                      member explanation videos
  README_SUBMISSION.txt       this file

No internet connection is needed at any point. Everything runs over TCP
(port 5050) and UDP discovery broadcasts (port 5051) on the local network.
Nothing needs to be installed: the executables bundle Python, Qt, the
fonts and the PDF renderer.

----------------------------------------------------------------------
QUICK START (any OS)
----------------------------------------------------------------------
  1. Extract the WHOLE zip first. Do not run the programs from inside the zip.
  2. On ONE machine start the server. It prints the address clients use,
     e.g.  "Clients on this LAN can connect to: 192.168.1.20:5050".
     (Alternative: start only the client, choose "Host new room" and tick
     "Start a server on this computer".)
  3. On every machine start the client. Pick the server under
     "Nearby servers" (or type the IP and port shown by the server),
     enter a name, then create a room or join one by its code.

----------------------------------------------------------------------
WINDOWS (10/11, 64-bit)            folder: executables\\{win}\\LANBoard
----------------------------------------------------------------------
  * Right-click the zip > "Extract All...". Extract to a short path such as
    C:\\LANBoard (very long paths can break extraction).
  * Double-click lanboard-server.exe (a console window shows the address),
    then lanboard-client.exe.
  * SmartScreen "Windows protected your PC": click "More info" > "Run anyway"
    (the executables are not code-signed).
  * Firewall: when Windows Defender Firewall asks, tick "Private networks"
    and click "Allow access". The network profile must be "Private"
    (Settings > Network & Internet > your network > Private).
    Without admin rights the prompt cannot be confirmed; then an admin can run:
      netsh advfirewall firewall add rule name="LANBoard TCP" dir=in action=allow protocol=TCP localport=5050
      netsh advfirewall firewall add rule name="LANBoard UDP" dir=in action=allow protocol=UDP localport=5051
    or host the server on a machine where you are admin. Clients only make
    outgoing connections and need no firewall rule.
  * Rooms are stored in %APPDATA%\\LANBoard.

----------------------------------------------------------------------
macOS (Apple Silicon unless the folder name says x64)
                                    folder: executables/{mac}/LANBoard
----------------------------------------------------------------------
  * Double-click the zip (Archive Utility keeps the permissions and links).
  * A downloaded zip is quarantined by Gatekeeper ("cannot be opened" /
    "developer cannot be verified"). Clear it once, in Terminal:
      xattr -dr com.apple.quarantine executables/{mac}/LANBoard
  * Run in Terminal:
      cd executables/{mac}/LANBoard
      ./lanboard-server            # host machine only
      ./lanboard-client            # every participant
  * Click "Allow" when macOS asks to accept incoming network connections.
  * Rooms are stored in ~/Library/Application Support/LANBoard.

----------------------------------------------------------------------
LINUX (x86-64, glibc; tested on Ubuntu)
                                    folder: executables/{lin}/LANBoard
----------------------------------------------------------------------
  * Extract with:  unzip LANBoard_CN_Project_2026.zip   (keeps the +x bits;
    if they are lost:  chmod +x lanboard-server lanboard-client)
  * Run:  ./lanboard-server   and   ./lanboard-client
  * If the client says it could not load the Qt platform plugin "xcb",
    the X11 libraries are missing. Install them BEFORE demo day (needs internet):
      sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \\
        libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libegl1
    On Wayland desktops  QT_QPA_PLATFORM=wayland ./lanboard-client  also works.
  * Firewall (if ufw is active):  sudo ufw allow 5050/tcp && sudo ufw allow 5051/udp
  * Rooms are stored in ~/.local/share/lanboard.

----------------------------------------------------------------------
INTERNET OFF / LAB NETWORK NOTES
----------------------------------------------------------------------
  * All machines must be on the same LAN segment: one switch, one Wi-Fi
    network, or a phone/laptop hotspot (the hotspot does not need internet).
  * Campus/guest Wi-Fi often blocks traffic between clients ("client
    isolation"). If machines cannot see each other, use a hotspot or switch.
  * If "Nearby servers" stays empty, broadcasts are being dropped: type the
    server's IP and port by hand (shown by the server at startup; on the
    host: ipconfig / ifconfig / ip addr).
  * Port 5050 already in use?  Start the server on another port, e.g.
      lanboard-server --port 5060
    and use that port in the clients (discovery shows the right port).
  * lanboard-server --help lists all options (--data DIR, --name, --dashboard, ...).

----------------------------------------------------------------------
RUN FROM SOURCE (alternative)
----------------------------------------------------------------------
  Python 3.12 with PySide6-Essentials, PySide6-Addons and pypdfium2 installed
  (pip needs internet, so prepare this beforehand), then from source/:
      python -m lanboard.server --port 5050
      python -m lanboard.client
  (set PYTHONPATH=src, or "pip install -e ." once). See source/README.md.
"""


VIDEO_NOTE = """Member explanation videos
=========================

Put one video per team member in this folder before submitting, e.g.

  01_<Name>_<RollNo>.mp4

Each member explains their own contribution using the presentation and the
source code, with name and face clearly visible (see docs/SPEC.md,
"Explanation Video").
"""


# --------------------------------------------------------------------------- main
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble the LANBoard submission zip")
    ap.add_argument("--out", default=str(ROOT / "release"), help="output folder (default: release/)")
    ap.add_argument("--dist", default=str(ROOT / "dist" / "LANBoard"), help="this machine's bundle")
    ap.add_argument("--no-local-dist", action="store_true", help="do not include this machine's dist/")
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "extra").mkdir(exist_ok=True)
    zip_path = out_dir / ZIP_NAME
    tmp_path = zip_path.with_suffix(".zip.part")
    print(f"Building {zip_path}")

    packer = Packer(tmp_path)
    platforms: list[str] = []
    try:
        print("executables:")
        dist = Path(args.dist)
        if not args.no_local_dist:
            if bundle_ok(dist):
                tag = platform_tag()
                head_time = int(git("log", "-1", "--format=%ct").decode().strip() or 0)
                exe = next(p for p in dist.iterdir() if p.name.startswith("lanboard-client"))
                if exe.stat().st_mtime < head_time:
                    warn(f"{dist} is older than the last commit: rebuild it with "
                         "pyinstaller --noconfirm packaging/lanboard.spec")
                packer.add_tree(dist, f"executables/{tag}/LANBoard")
                platforms.append(tag)
                print(f"  + executables/{tag}/LANBoard  (from {dist})")
            else:
                warn(f"{dist} has no lanboard-server/lanboard-client: build it first")
        for extra in sorted(p for p in (out_dir / "extra").iterdir() if p.is_dir()):
            if extra.name in platforms:
                warn(f"release/extra/{extra.name} skipped: this machine's bundle already uses that name")
                continue
            if add_extra_platform(packer, extra):
                platforms.append(extra.name)
        if not platforms:
            warn("no executables at all in the zip")
        elif not any(p.startswith("windows") for p in platforms):
            warn("no Windows build included (lab PCs may be Windows): download the CI artifact "
                 "LANBoard-Windows and put it in release/extra/windows-x64/")

        print("source:")
        commit = add_source(packer)
        print(f"  + source/  (git archive of {commit})")

        print("docs:")
        docs_found = {}
        for name in DOCS:
            p = find_doc(name, out_dir)
            docs_found[name] = p is not None
            if p is None:
                warn(f"docs/{name} not found (looked in docs/, repo root, release/docs/)")
                continue
            packer.add_bytes(f"docs/{name}", p.read_bytes(), 0o644)
            print(f"  + docs/{name}  (from {p})")

        packer.add_bytes("video/README.txt", VIDEO_NOTE.encode("utf-8"))
        packer.add_bytes("README_SUBMISSION.txt",
                         readme_text(platforms, docs_found, commit).replace("\n", "\r\n").encode("utf-8"))
        packer.close()
        os.replace(tmp_path, zip_path)
    except BaseException:
        packer.zf.close()
        tmp_path.unlink(missing_ok=True)
        raise

    # ------------------------------------------------------------ summary
    print("\nSize summary (uncompressed):")
    for section in sorted(packer.sizes):
        print(f"  {section:<40} {human(packer.sizes[section]):>10}  {packer.counts[section]:>5} files")
    total = sum(packer.sizes.values())
    print(f"  {'TOTAL':<40} {human(total):>10}")
    print(f"Zip: {zip_path}  {human(zip_path.stat().st_size)}")
    print(f"Longest path in zip: {len(packer.longest)} chars ({packer.longest})")
    if len(packer.longest) > WIN_PATH_WARN:
        warn(f"a path in the zip is {len(packer.longest)} chars long; extract to a short folder on Windows")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
