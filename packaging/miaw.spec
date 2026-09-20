# PyInstaller-recept voor de MIAW Marktplaats Monitor.
#
# Bouwen kan alleen op het systeem waarvoor je bouwt: een Windows-exe maak je op
# Windows, een macOS-app op macOS. PyInstaller kan niet kruislings bouwen.
#
#   pyinstaller packaging/miaw.spec --noconfirm
#
# Zie packaging/README.md voor de bouwstappen per systeem.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

PROJECT_DIR = Path(SPECPATH).resolve().parent
IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# keyring zoekt zijn backends pas tijdens het draaien op. PyInstaller ziet die
# imports dus niet staan en laat ze zonder deze regel weg; de app valt dan terug
# op het leesbaar opslaan van de token.
hidden = collect_submodules("keyring.backends")
hidden += ["keyring.backends.null"]
if IS_WINDOWS:
    hidden += ["keyring.backends.Windows", "win32ctypes.core"]
elif IS_MACOS:
    hidden += ["keyring.backends.macOS"]
else:
    hidden += ["keyring.backends.SecretService", "secretstorage", "jeepney"]

a = Analysis(
    [str(PROJECT_DIR / "main.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # Qt-onderdelen die deze app niet gebruikt. Scheelt ruim honderd MB.
    excludes=[
        "PyQt6.QtQml",
        "PyQt6.QtQuick",
        "PyQt6.QtQuick3D",
        "PyQt6.QtMultimedia",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.Qt3DCore",
        "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization",
        "PyQt6.QtPdf",
        "PyQt6.QtBluetooth",
        "PyQt6.QtDesigner",
        "PyQt6.QtTest",
        "tkinter",
        "unittest",
        "pydoc",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MIAW Marktplaats Monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Geen zwart terminalvenster naast de app op Windows.
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MIAW Marktplaats Monitor",
)

if IS_MACOS:
    app = BUNDLE(
        coll,
        name="MIAW Marktplaats Monitor.app",
        bundle_identifier="nl.miaw.marktplaatsmonitor",
        info_plist={
            "CFBundleShortVersionString": "4.0",
            "NSHighResolutionCapable": True,
            # Zonder dit weigert macOS de netwerkverbinding in een app-bundel.
            "LSMinimumSystemVersion": "11.0",
        },
    )
