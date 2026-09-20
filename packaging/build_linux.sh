#!/usr/bin/env bash
# Builds the Linux version. Run this on Linux.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

python3 -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements.txt pyinstaller

rm -rf build dist
.venv-build/bin/python -m PyInstaller packaging/miaw.spec --noconfirm

# Everything in one tar.gz, ready to hand over.
cd dist
tar czf "MIAW-Marktplaats-Monitor-linux.tar.gz" "MIAW Marktplaats Monitor"
cd ..
echo
echo "Done: dist/MIAW-Marktplaats-Monitor-linux.tar.gz"
echo "Unpack and start with:  './MIAW Marktplaats Monitor/MIAW Marktplaats Monitor'"
