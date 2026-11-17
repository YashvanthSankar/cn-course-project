import os
import sys


ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")

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

PY_OPTIONS = [("X utf8=1", None, "OPTION")]

server_a = Analysis([os.path.join(SPECPATH, "server_entry.py")], pathex=[SRC],
                    excludes=["PySide6", "shiboken6", "tkinter"], noarchive=False)
CLIENT_DATAS = [(os.path.join(SRC, "lanboard", "client", "assets"), os.path.join("lanboard", "client", "assets"))]

client_a = Analysis([os.path.join(SPECPATH, "client_entry.py")], pathex=[SRC],
                    datas=CLIENT_DATAS,
                    hiddenimports=["PySide6.QtSvg", "pypdfium2"],
                    excludes=QT_EXCLUDES, noarchive=False)

if sys.platform == "win32":
    server_a.binaries = [entry for entry in server_a.binaries if os.path.basename(entry[0]).lower() != "icuuc.dll"]
    client_a.binaries = [entry for entry in client_a.binaries if os.path.basename(entry[0]).lower() != "icuuc.dll"]

server_pyz = PYZ(server_a.pure)
client_pyz = PYZ(client_a.pure)

server_exe = EXE(server_pyz, server_a.scripts, PY_OPTIONS, exclude_binaries=True,
                 name="lanboard-server", console=True, upx=False)
client_exe = EXE(client_pyz, client_a.scripts, PY_OPTIONS, exclude_binaries=True,
                 name="lanboard-client", console=False, upx=False,
                 argv_emulation=False)

coll = COLLECT(server_exe, server_a.binaries, server_a.datas,
               client_exe, client_a.binaries, client_a.datas,
               upx=False, name="LANBoard")

if sys.platform == "darwin":
    import re
    with open(os.path.join(SRC, "lanboard", "__init__.py"), encoding="utf-8") as f:
        VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)
    ICNS = os.path.join(SPECPATH, "LANBoard.icns")
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
                     "LSBackgroundOnly": False,
                     "LSMinimumSystemVersion": "11.0",
                     "NSLocalNetworkUsageDescription":
                         "LANBoard connects to whiteboard servers on your local network "
                         "and finds them with a LAN broadcast.",
                 })
