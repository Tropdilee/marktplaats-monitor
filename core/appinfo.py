"""Application name and version, in one place.

These used to live only in ui/main_window.py, but core/paths.py and the
self-test need them as well to work out the data folder. Two copies drifting
apart would produce two different folders.
"""

APP_NAME = "MIAW Marktplaats Monitor"
ORG_NAME = "PerplexityLocal"
APP_VERSION = "4.0"
LAST_UPDATE = "2026-09-20"
