"""Controle of de app op deze computer alles kan wat hij nodig heeft.

Bedoeld voor wie de app krijgt en iets niet werkt. Start met `--selftest` en je
krijgt per onderdeel te zien of het in orde is. Het verslag wordt ook naast de
gegevens weggeschreven, zodat het op Windows terug te vinden is als er geen
terminalvenster is.
"""

import platform
import sys
from datetime import datetime

from core.translations import set_language, tr


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
        merk = tr("st_bundled_yes") if is_frozen() else tr("st_bundled_no")
        return True, f"{pad} ({merk})"
    except OSError as exc:
        return False, f'{pad} {tr("st_not_writable")}: {exc}'


def _check_sleutelbos():
    try:
        import keyring
    except Exception as exc:
        return False, f'{tr("st_no_keyring_pkg")}: {exc}'

    try:
        backend = keyring.get_keyring()
        naam = type(backend).__name__
        keyring.get_password("MIAW Marktplaats Monitor", "selftest")
        if "fail" in type(backend).__module__.lower():
            return False, tr("st_no_keyring").format(naam=naam)
        return True, naam
    except Exception as exc:
        return False, tr("st_keyring_error").format(fout=type(exc).__name__)


def _check_marktplaats():
    try:
        from core.monitor import MarktplaatsMonitor

        monitor = MarktplaatsMonitor()
        # Zonder promotiefilter: deze controle gaat over de verbinding, niet
        # over het filteren. Brede termen als "fiets" bestaan in de eerste
        # honderd resultaten volledig uit betaalde promoties.
        items = monitor.fetch_listings("fiets", limit=3, hide_promoted=False)
        if items:
            return True, tr("st_results").format(aantal=len(items))
        return False, tr("st_no_results")
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
        return None, tr("st_no_token")
    if not looks_like_token(token):
        return False, tr("st_token_shape")

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
    # Zelfde taal als in de app is ingesteld.
    from PyQt6.QtCore import QSettings

    from core.appinfo import APP_NAME, ORG_NAME

    set_language(QSettings(ORG_NAME, APP_NAME).value("ui/language", "Nederlands"))

    regels = [
        f"{APP_NAME} - {tr('st_title')}",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        f"  {platform.platform()}",
        f"  Python {sys.version.split()[0]}",
        "",
    ]

    controles = [
        (tr("st_qt"), _check_qt),
        (tr("st_datadir"), _check_gegevensmap),
        (tr("st_keyring"), _check_sleutelbos),
        (tr("st_connection"), _check_marktplaats),
        (tr("st_telegram"), _check_telegram),
    ]

    mislukt = 0
    for naam, functie in controles:
        try:
            ok, toelichting = functie()
        except Exception as exc:
            ok, toelichting = False, f'{tr("st_unexpected")}: {type(exc).__name__}: {exc}'
        if ok is None:
            regels.append(f"  [ -  ] {naam} - {toelichting}")
            continue
        if not ok:
            mislukt += 1
        regels.append(_regel(naam, ok, toelichting))

    regels.append("")
    regels.append(
        tr("st_all_ok") if not mislukt else tr("st_failed").format(aantal=mislukt)
    )
    verslag = "\n".join(regels)
    print(verslag)

    try:
        from core.paths import data_file

        bestand = data_file("selftest.txt")
        bestand.write_text(verslag + "\n", encoding="utf-8")
        print(f'\n{tr("st_saved_as")}\n  {bestand}')
    except Exception:
        pass

    return 0 if not mislukt else 1
