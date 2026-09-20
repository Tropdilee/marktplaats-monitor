"""Controle of de app op deze computer alles kan wat hij nodig heeft.

Bedoeld voor wie de app krijgt en iets niet werkt. Start met `--selftest` en je
krijgt per onderdeel te zien of het in orde is. Het verslag wordt ook naast de
gegevens weggeschreven, zodat het op Windows terug te vinden is als er geen
terminalvenster is.
"""

import platform
import sys
from datetime import datetime


def _regel(naam, ok, toelichting=""):
    merk = "OK  " if ok else "FOUT"
    return f"  [{merk}] {naam}" + (f" - {toelichting}" if toelichting else "")


def _check_gegevensmap():
    from core.paths import data_dir, is_frozen

    pad = data_dir()
    proef = pad / ".schrijftest"
    try:
        proef.write_text("x", encoding="utf-8")
        proef.unlink()
        return True, f"{pad} (gebundeld: {'ja' if is_frozen() else 'nee'})"
    except OSError as exc:
        return False, f"{pad} niet beschrijfbaar: {exc}"


def _check_sleutelbos():
    try:
        import keyring
    except Exception as exc:
        return False, f"keyring ontbreekt in deze build: {exc}"

    try:
        backend = keyring.get_keyring()
        naam = type(backend).__name__
        keyring.get_password("MIAW Marktplaats Monitor", "selftest")
        if "fail" in type(backend).__module__.lower():
            return False, f"geen bruikbare sleutelbos ({naam}); token komt leesbaar op schijf"
        return True, naam
    except Exception as exc:
        return False, f"sleutelbos niet bruikbaar: {type(exc).__name__}; token komt leesbaar op schijf"


def _check_marktplaats():
    try:
        from core.monitor import MarktplaatsMonitor

        monitor = MarktplaatsMonitor()
        # Zonder promotiefilter: deze controle gaat over de verbinding, niet
        # over het filteren. Brede termen als "fiets" bestaan in de eerste
        # honderd resultaten volledig uit betaalde promoties.
        items = monitor.fetch_listings("fiets", limit=3, hide_promoted=False)
        if items:
            return True, f"{len(items)} resultaten opgehaald"
        return False, "verbinding gelukt maar geen resultaten"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _check_telegram():
    from PyQt6.QtCore import QSettings

    from core.appinfo import APP_NAME, ORG_NAME
    from core.secrets import SecretStore
    from core.telegram_client import get_bot_info, looks_like_token

    instellingen = QSettings(ORG_NAME, APP_NAME)
    token = SecretStore(instellingen).get_token()
    if not token:
        return None, "geen token ingesteld (overslaan)"
    if not looks_like_token(token):
        return False, "token heeft niet de vorm die BotFather geeft"

    ok, payload = get_bot_info(token)
    if ok:
        return True, "@" + (payload.get("result") or {}).get("username", "?")
    from core.telegram_client import describe_error

    return False, describe_error(payload)


def _check_qt():
    try:
        from PyQt6.QtCore import QT_VERSION_STR
        from PyQt6.QtWidgets import QApplication

        if QApplication.instance() is None:
            QApplication([])
        return True, f"Qt {QT_VERSION_STR}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run():
    regels = [
        "MIAW Marktplaats Monitor - zelftest",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        f"  {platform.platform()}",
        f"  Python {sys.version.split()[0]}",
        "",
    ]

    controles = [
        ("Qt en vensterbeheer", _check_qt),
        ("Gegevensmap", _check_gegevensmap),
        ("Sleutelbos voor de token", _check_sleutelbos),
        ("Verbinding met Marktplaats", _check_marktplaats),
        ("Telegram-bot", _check_telegram),
    ]

    mislukt = 0
    for naam, functie in controles:
        try:
            ok, toelichting = functie()
        except Exception as exc:
            ok, toelichting = False, f"onverwachte fout: {type(exc).__name__}: {exc}"
        if ok is None:
            regels.append(f"  [ -  ] {naam} - {toelichting}")
            continue
        if not ok:
            mislukt += 1
        regels.append(_regel(naam, ok, toelichting))

    regels.append("")
    regels.append("Alles in orde." if not mislukt else f"{mislukt} onderdeel(en) niet in orde.")
    verslag = "\n".join(regels)
    print(verslag)

    try:
        from core.paths import data_file

        bestand = data_file("selftest.txt")
        bestand.write_text(verslag + "\n", encoding="utf-8")
        print(f"\nVerslag ook opgeslagen als:\n  {bestand}")
    except Exception:
        pass

    return 0 if not mislukt else 1
