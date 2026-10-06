"""Application name, version and identity, in one place.

These used to live only in ui/main_window.py, but core/paths.py, the self-test
and the HTTP clients need them as well. Copies drifting apart would produce
different folders and different User-Agent strings.
"""

APP_NAME = "MIAW Marktplaats Monitor"
ORG_NAME = "MIAW"
APP_VERSION = "4.1"
LAST_UPDATE = "2026-10-06"

PROJECT_URL = "https://github.com/Tropdilee/marktplaats-monitor"

# Earlier versions stored settings and data under this organisation name. It is
# only kept so core/migrate.py can move existing installs across.
LEGACY_ORG_NAMES = ("PerplexityLocal",)

# Every request says which program sent it and where to find it, instead of
# posing as a web browser.
USER_AGENT = f"MIAW-Marktplaats-Monitor/{APP_VERSION} (+{PROJECT_URL})"
