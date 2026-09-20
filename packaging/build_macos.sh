#!/usr/bin/env bash
# Builds the macOS version. Run this on macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements.txt pyinstaller

rm -rf build dist
.venv-build/bin/python -m PyInstaller packaging/miaw.spec --noconfirm

# A disk image is the usual way to hand an app over on macOS.
hdiutil create -volname "MIAW Marktplaats Monitor" \
  -srcfolder "dist/MIAW Marktplaats Monitor.app" \
  -ov -format UDZO "dist/MIAW-Marktplaats-Monitor-macos.dmg"

echo
echo "Done: dist/MIAW-Marktplaats-Monitor-macos.dmg"
echo "The app is unsigned: the first time, right-click -> Open."
