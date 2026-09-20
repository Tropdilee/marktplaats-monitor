# De app inpakken om door te geven

*Nederlands · [English](README.en.md)*

De ontvanger hoeft geen Python te installeren: de bundel bevat Python, PyQt6 en
alle pakketten. Uitpakken en starten is genoeg.

## Belangrijk: bouwen kan alleen op het doelsysteem

PyInstaller bouwt voor het systeem waarop het draait. Een Windows-exe maak je
dus op Windows, een macOS-app op macOS. Er is geen manier om ze alle drie vanaf
één computer te maken.

Twee mogelijkheden:

1. **Met GitHub Actions** — `.github/workflows/build.yml` bouwt alle drie
   tegelijk op machines van GitHub. Dat is de eenvoudigste weg als je niet zelf
   een Windows- en een Mac-computer hebt. Push een tag:

   ```bash
   git tag v4.0 && git push origin v4.0
   ```

   De drie bestanden verschijnen daarna onder Releases. Zonder tag kun je de
   workflow ook met de hand starten via het tabblad Actions; de bestanden hangen
   dan onder die uitvoering.

2. **Zelf, per systeem** — gebruik het script voor dat systeem:

   | Systeem | Script | Resultaat |
   | --- | --- | --- |
   | Linux | `packaging/build_linux.sh` | `dist/MIAW-Marktplaats-Monitor-linux.tar.gz` |
   | Windows | `packaging\build_windows.bat` | `dist\MIAW-Marktplaats-Monitor-windows.zip` |
   | macOS | `packaging/build_macos.sh` | `dist/MIAW-Marktplaats-Monitor-macos.dmg` |

   Elk script maakt een aparte bouw-virtualenv (`.venv-build`), zodat je eigen
   `.venv` er niet door verandert.

## Wat de ontvanger moet doen

**Linux** — uitpakken en starten:

```bash
tar xzf MIAW-Marktplaats-Monitor-linux.tar.gz
"./MIAW Marktplaats Monitor/MIAW Marktplaats Monitor"
```

Klaagt de app over het `xcb`-platform, dan ontbreekt er een systeembibliotheek:

```bash
sudo apt install libxcb-cursor0
```

**Windows** — zip uitpakken en `MIAW Marktplaats Monitor.exe` starten. De bundel
is niet ondertekend, dus SmartScreen komt met "Windows heeft uw pc beveiligd":
klik op *Meer informatie* en daarna op *Toch uitvoeren*.

**macOS** — dmg openen en de app naar Programma's slepen. Ook hier geen
handtekening: de eerste keer rechtsklikken op de app en *Openen* kiezen, anders
weigert Gatekeeper.

## Werkt het niet? Zelftest

De app kan zichzelf doorlichten zonder dat het venster opengaat:

```bash
"./MIAW Marktplaats Monitor/MIAW Marktplaats Monitor" --selftest
```

Op Windows vanuit PowerShell in de uitgepakte map:

```powershell
& ".\MIAW Marktplaats Monitor.exe" --selftest
```

Je krijgt per onderdeel te zien of het in orde is:

```
  [OK  ] Qt en vensterbeheer - Qt 6.11.0
  [OK  ] Gegevensmap - /home/...//MIAW Marktplaats Monitor (gebundeld: ja)
  [OK  ] Sleutelbos voor de token - Keyring
  [OK  ] Verbinding met Marktplaats - 3 resultaten opgehaald
  [OK  ] Telegram-bot - @jouwbot
```

Het verslag komt ook in `selftest.txt` in de gegevensmap te staan, handig op
Windows waar geen terminalvenster meebomt.

## Waar de gegevens staan

In een gebundelde app niet naast het programma, maar bij de gebruiker:

| Systeem | Map |
| --- | --- |
| Linux | `~/.local/share/PerplexityLocal/MIAW Marktplaats Monitor/` |
| Windows | `%LOCALAPPDATA%\PerplexityLocal\MIAW Marktplaats Monitor\` |
| macOS | `~/Library/Application Support/PerplexityLocal/MIAW Marktplaats Monitor/` |

Daar komen `seen_ids.json`, `categories.json`, `image_cache/` en de opgeslagen
lijsten terecht. Instellingen staan los daarvan in QSettings, en de Telegram-token
in de sleutelbos van het systeem.

Bij het starten vanuit de broncode blijft alles gewoon in `data/` naast de code
staan, zodat ontwikkelen en gebruiken elkaar niet in de weg zitten.

## Omvang

De Linux-bundel is ongeveer 172 MB, grotendeels Qt. In `packaging/miaw.spec`
staan de Qt-onderdelen die eruit gelaten worden (QtQuick, QtWebEngine,
multimedia en dergelijke); dat scheelt al ruim honderd MB. Wie verder wil
snoeien, kan daar kijken.

**Geef de token niet mee.** Die staat in jouw sleutelbos, niet in de bundel. Wie
de app krijgt maakt zijn eigen bot bij BotFather; zie het hoofdstuk over
Telegram in de README.
