#!/usr/bin/env bash
# Start de MIAW Marktplaats Monitor op Linux.
# Vervangt start.bat, dat nog naar C:\Marktplaats_desktop_app verwees.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [ ! -x .venv/bin/python ]; then
    echo "Geen virtualenv gevonden, bezig met aanmaken..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python main.py
