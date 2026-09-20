#!/usr/bin/env bash
# Starts the MIAW Marktplaats Monitor on Linux.
# Replaces start.bat, which still pointed at C:\Marktplaats_desktop_app.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [ ! -x .venv/bin/python ]; then
    echo "No virtualenv found, creating one..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python main.py
