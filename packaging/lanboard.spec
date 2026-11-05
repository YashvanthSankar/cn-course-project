# PyInstaller spec: builds BOTH standalone executables into one folder
#   dist/LANBoard/lanboard-server[.exe]   (console app, no Qt)
#   dist/LANBoard/lanboard-client[.exe]   (GUI app)
# They share one _internal/ runtime. The client's "start a server on this
# computer" option finds lanboard-server next to itself.
#
# Build:  .venv/bin/pyinstaller --noconfirm packaging/lanboard.spec
import os

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

server_a = Analysis([os.path.join(SPECPATH, "server_entry.py")], pathex=[SRC],
                    excludes=["PySide6", "shiboken6", "tkinter"], noarchive=False)
client_a = Analysis([os.path.join(SPECPATH, "client_entry.py")], pathex=[SRC],
                    hiddenimports=["PySide6.QtPdf", "PySide6.QtSvg"],
                    excludes=QT_EXCLUDES, noarchive=False)

server_pyz = PYZ(server_a.pure)
client_pyz = PYZ(client_a.pure)

server_exe = EXE(server_pyz, server_a.scripts, [], exclude_binaries=True,
                 name="lanboard-server", console=True)
client_exe = EXE(client_pyz, client_a.scripts, [], exclude_binaries=True,
                 name="lanboard-client", console=False)

coll = COLLECT(server_exe, server_a.binaries, server_a.datas,
               client_exe, client_a.binaries, client_a.datas,
               name="LANBoard")
