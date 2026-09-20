"""Where the application stores its data.

While developing, everything lives in the `data/` folder next to the source.
That is convenient, but in a bundled app (PyInstaller) `__file__` points at a
temporary folder that disappears on exit and cannot be written to anyway. That
case needs a real per-user folder:

- Linux   ~/.local/share/PerplexityLocal/MIAW Marktplaats Monitor
- Windows C:\\Users\\<name>\\AppData\\Local\\PerplexityLocal\\MIAW Marktplaats Monitor
- macOS   ~/Library/Application Support/PerplexityLocal/MIAW Marktplaats Monitor

QStandardPaths picks the right one per system, so no extra package is needed.
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from core.appinfo import APP_NAME, ORG_NAME

FALLBACK_DIR_NAME = ".miaw-marktplaats-monitor"


def is_frozen():
    """True when the app is running as a bundled executable."""
    return bool(getattr(sys, "frozen", False))


def data_dir():
    """The folder that data may be written to."""
    if is_frozen():
        # Deliberately GenericDataLocation plus our own names appended.
        # AppDataLocation only appends those names once a QApplication has set
        # them, and under `--selftest` no such application exists yet - which
        # dumped everything loose into ~/.local/share.
        locatie = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericDataLocation
        )
        pad = (
            Path(locatie) / ORG_NAME / APP_NAME
            if locatie
            else Path.home() / FALLBACK_DIR_NAME
        )
    else:
        pad = Path(__file__).resolve().parents[1] / "data"

    try:
        pad.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Failing to create it must not stop the app; the individual parts
        # handle a failed write themselves.
        pass
    return pad


def data_file(*delen):
    """Path to a file or folder inside the data folder."""
    return data_dir().joinpath(*delen)
