#!/usr/bin/env bash
# Bouwt de macOS-versie. Draai dit op macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements.txt pyinstaller

rm -rf build dist
.venv-build/bin/python -m PyInstaller packaging/miaw.spec --noconfirm

# Een schijfkopie is op macOS de gebruikelijke manier om een app door te geven.
hdiutil create -volname "MIAW Marktplaats Monitor" \
  -srcfolder "dist/MIAW Marktplaats Monitor.app" \
  -ov -format UDZO "dist/MIAW-Marktplaats-Monitor-macos.dmg"

echo
echo "Klaar: dist/MIAW-Marktplaats-Monitor-macos.dmg"
echo "De app is niet ondertekend: bij de eerste keer openen rechtsklikken -> Openen."
