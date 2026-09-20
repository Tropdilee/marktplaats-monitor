"""Waar de app zijn gegevens bewaart.

Tijdens het ontwikkelen staat alles in de map `data/` naast de broncode. Dat is
handig, maar in een gebundelde app (PyInstaller) wijst `__file__` naar een
tijdelijke map die bij het afsluiten weer verdwijnt en waar bovendien niet in
geschreven mag worden. Daar hoort een echte gebruikersmap bij:

- Linux   ~/.local/share/MIAW Marktplaats Monitor
- Windows C:\\Users\\<naam>\\AppData\\Roaming\\PerplexityLocal\\MIAW Marktplaats Monitor
- macOS   ~/Library/Application Support/MIAW Marktplaats Monitor

QStandardPaths kiest die per systeem, dus daar is geen extra pakket voor nodig.
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from core.appinfo import APP_NAME, ORG_NAME

FALLBACK_DIR_NAME = ".miaw-marktplaats-monitor"


def is_frozen():
    """True als de app als gebundelde executable draait."""
    return bool(getattr(sys, "frozen", False))


def data_dir():
    """De map waar gegevens in geschreven mogen worden."""
    if is_frozen():
        # Bewust GenericDataLocation plus de eigen namen erachter. AppDataLocation
        # plakt die namen er alleen aan als een QApplication ze al gezet heeft, en
        # bij `--selftest` bestaat die nog niet - dan belandde alles los in
        # ~/.local/share.
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
        # Niet kunnen aanmaken mag de app niet tegenhouden; de losse onderdelen
        # vangen een mislukte schrijfactie zelf op.
        pass
    return pad


def data_file(*delen):
    """Pad naar een bestand of map binnen de gegevensmap."""
    return data_dir().joinpath(*delen)
