#!/usr/bin/env bash
# Bouwt de Linux-versie. Draai dit op Linux.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

python3 -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements.txt pyinstaller

rm -rf build dist
.venv-build/bin/python -m PyInstaller packaging/miaw.spec --noconfirm

# Alles in één tar.gz, klaar om door te geven.
cd dist
tar czf "MIAW-Marktplaats-Monitor-linux.tar.gz" "MIAW Marktplaats Monitor"
cd ..
echo
echo "Klaar: dist/MIAW-Marktplaats-Monitor-linux.tar.gz"
echo "Uitpakken en starten met:  './MIAW Marktplaats Monitor/MIAW Marktplaats Monitor'"
