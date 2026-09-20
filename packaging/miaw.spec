# PyInstaller recipe for the MIAW Marktplaats Monitor.
#
# You can only build on the system you are building for: a Windows exe is made
# on Windows, a macOS app on macOS. PyInstaller cannot cross-compile.
#
#   pyinstaller packaging/miaw.spec --noconfirm
#
# See packaging/README.en.md for the build steps per system.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

PROJECT_DIR = Path(SPECPATH).resolve().parent
IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# keyring resolves its backends only at runtime. PyInstaller therefore does not
# see those imports and would leave them out without this line; the app would
# then fall back to storing the token in readable form.
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
    # Qt components this app does not use. Saves well over a hundred MB.
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
    # No black terminal window next to the app on Windows.
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
            # Without this macOS refuses network access inside an app bundle.
            "LSMinimumSystemVersion": "11.0",
        },
    )
