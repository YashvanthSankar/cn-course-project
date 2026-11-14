# PyInstaller spec: builds BOTH standalone executables into one folder
#   dist/LANBoard/lanboard-server[.exe]   (console app, no Qt)
#   dist/LANBoard/lanboard-client[.exe]   (GUI app)
# They share one _internal/ runtime. The client's "start a server on this
# computer" option finds lanboard-server next to itself
# (client/ui/server_launcher.py: Path(sys.executable).with_name("lanboard-server[.exe]")),
# so the two executables must stay in the same folder.
#
# On macOS the spec ALSO builds dist/LANBoard.app (double-clickable, with an
# icon; packaging/make_dmg.py wraps it in a .dmg). Both executables go into
# LANBoard.app/Contents/MacOS, so the sibling lookup above still works, and the
# client is listed first so it becomes CFBundleExecutable, and LSBackgroundOnly
# is forced off (otherwise the console server would make it a background app).
#
# Build (same command on Windows, macOS and Linux; PyInstaller cannot
# cross-compile, so each OS builds its own bundle, see .github/workflows/build.yml):
#     .venv/bin/pyinstaller --noconfirm packaging/lanboard.spec        (macOS / Linux)
#     .venv\Scripts\pyinstaller --noconfirm packaging\lanboard.spec    (Windows)
#
# Cross-platform notes:
#   * EXE names have no extension: PyInstaller adds ".exe" on Windows by itself.
#   * Only os.path.join is used for paths, never "/" or "\\".
#   * The window icon is drawn at run time by ui/theme.app_icon(). Only the
#     macOS .app uses an icon file, packaging/LANBoard.icns (make_icns.py);
#     the build still works without it.
#   * upx is off: UPX-packed Qt DLLs are a classic cause of antivirus false
#     positives on Windows lab PCs, and UPX breaks code signing on macOS.
import os
import sys


ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")

# Qt modules we never use: excluding them saves 100+ MB
QT_EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DAnimation",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtTextToSpeech", "PySide6.QtDesigner", "PySide6.QtHelp", "tkinter",
]

# Run both executables in Python's UTF-8 mode (PEP 540). On Windows the
# default text encoding is the ANSI code page (cp1252 etc.); the embedded
# server's stdout/stderr is redirected to a log file by the client, and a
# non-ASCII user or room name in a log line would otherwise raise
# UnicodeEncodeError inside logging. No effect on macOS/Linux (already UTF-8).
PY_OPTIONS = [("X utf8=1", None, "OPTION")]

server_a = Analysis([os.path.join(SPECPATH, "server_entry.py")], pathex=[SRC],
                    excludes=["PySide6", "shiboken6", "tkinter"], noarchive=False)
# UI fonts (Inter + JetBrains Mono, SIL OFL 1.1): theme.load_fonts() finds them under
# sys._MEIPASS/lanboard/client/assets/fonts in the frozen app
CLIENT_DATAS = [(os.path.join(SRC, "lanboard", "client", "assets"), os.path.join("lanboard", "client", "assets"))]

client_a = Analysis([os.path.join(SPECPATH, "client_entry.py")], pathex=[SRC],
                    datas=CLIENT_DATAS,
                    # pypdfium2 is imported lazily inside CanvasView.import_pdf(), so
                    # name it here; its hook bundles libpdfium + version.json files
                    hiddenimports=["PySide6.QtSvg", "pypdfium2"],
                    excludes=QT_EXCLUDES, noarchive=False)

# On Windows, Qt uses the system ICU shim. Python 3.14 may cause PyInstaller
# to collect a different icuuc.dll, whose exports are incompatible with Qt.
# Leaving that DLL in the bundle prevents the client from starting.
if sys.platform == "win32":
    server_a.binaries = [entry for entry in server_a.binaries if os.path.basename(entry[0]).lower() != "icuuc.dll"]
    client_a.binaries = [entry for entry in client_a.binaries if os.path.basename(entry[0]).lower() != "icuuc.dll"]

server_pyz = PYZ(server_a.pure)
client_pyz = PYZ(client_a.pure)

# console=True: the server is a terminal program (prints its LAN address, Ctrl+C stops it).
server_exe = EXE(server_pyz, server_a.scripts, PY_OPTIONS, exclude_binaries=True,
                 name="lanboard-server", console=True, upx=False)
# console=False: no extra terminal window for the GUI on Windows. sys.stdout/stderr
# are None then; the client never writes to them directly (print() is a no-op).
client_exe = EXE(client_pyz, client_a.scripts, PY_OPTIONS, exclude_binaries=True,
                 name="lanboard-client", console=False, upx=False,
                 # macOS: no argv emulation (it would swallow our --room/--user flags)
                 argv_emulation=False)

coll = COLLECT(server_exe, server_a.binaries, server_a.datas,
               client_exe, client_a.binaries, client_a.datas,
               upx=False, name="LANBoard")

if sys.platform == "darwin":
    import re
    with open(os.path.join(SRC, "lanboard", "__init__.py"), encoding="utf-8") as f:
        VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)
    ICNS = os.path.join(SPECPATH, "LANBoard.icns")
    # client first: BUNDLE takes CFBundleExecutable from the first EXE in the TOC
    app_coll = COLLECT(client_exe, client_a.binaries, client_a.datas,
                       server_exe, server_a.binaries, server_a.datas,
                       upx=False, name="LANBoard-app")
    app = BUNDLE(app_coll, name="LANBoard.app",
                 icon=ICNS if os.path.exists(ICNS) else None,
                 bundle_identifier="in.ac.cn.lanboard",
                 version=VERSION,
                 info_plist={
                     "CFBundleDisplayName": "LANBoard",
                     "CFBundleShortVersionString": VERSION,
                     "NSHighResolutionCapable": True,
                     # COLLECT takes console mode from its LAST exe (the server), which
                     # would make PyInstaller hide the app: it is a normal GUI app
                     "LSBackgroundOnly": False,
                     "LSMinimumSystemVersion": "11.0",
                     # macOS 15+ asks before an app talks to other machines on the
                     # LAN; this is the text shown in that prompt
                     "NSLocalNetworkUsageDescription":
                         "LANBoard connects to whiteboard servers on your local network "
                         "and finds them with a LAN broadcast.",
                 })
