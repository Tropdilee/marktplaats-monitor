# MIAW Marktplaats Monitor v4.0

*Nederlands · [English](README.en.md)*

Desktopapp (PyQt6) die Marktplaats in de gaten houdt en een Telegram-bericht
stuurt zodra er een nieuwe advertentie verschijnt die aan je zoekopdracht
voldoet.

Het venster is in het Nederlands en het Engels te gebruiken; wisselen kan via
**Taal / Language** in de menubalk of op het tabblad Weergave.

## Starten

Op Linux:

```bash
./start.sh
```

Het script maakt zo nodig de virtualenv aan, installeert de requirements en
start de app. Voor Windows en macOS staan aparte scripts in `scripts/`.

Handmatig kan ook:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Als PyQt6 klaagt over het `xcb`-platform ontbreekt er een systeembibliotheek:

```bash
sudo apt install libxcb-cursor0
```

## Hoe het zoeken werkt

De app gebruikt de interne zoek-API van Marktplaats (`/lrp/api/search`) en niet
de gewone zoekpagina. Dat is nodig omdat de HTML-pagina elk zoekargument
negeert: sortering, maximumprijs, afstand en aantal resultaten hebben daar geen
effect, en zonder sortering op datum komt een verse advertentie vaak niet eens
in de eerste dertig resultaten terecht.

Via de API werkt wel:

| Instelling | Hoe het meegaat |
| --- | --- |
| Sortering | `sortBy=SORT_INDEX&sortOrder=DECREASING`, nieuwste eerst |
| Max. prijs | `attributeRanges[]=PriceCents:0:N` |
| Regio + afstand | `postcode` samen met `distanceMeters` |
| Categorie | `l1CategoryId`, subcategorie via `l2CategoryIds[]` |
| Aantal resultaten | maximaal 100 per verzoek, meer via `offset` |

Valt de API weg, dan leest de app het `__NEXT_DATA__`-blok van de gewone
zoekpagina uit. Dat levert dezelfde gegevens op, maar ongefilterd en maximaal
dertig resultaten.

### Let op

`robots.txt` van Marktplaats sluit `/lrp/api/search` uit voor geautomatiseerd
verkeer. De gewone zoekpagina is wel toegestaan, maar kan niet op datum
sorteren en is daardoor ongeschikt voor dit doel. De app blijft bewust ruim
onder normaal surfgedrag (zie hieronder), maar dat maakt het gebruik niet
alsnog toegestaan. Die afweging ligt bij jou.

## Zoeken en filteren

- **Zoekterm** — vrije tekst, net als op de site.
- **Categorie en subcategorie** — hoofdcategorieën komen uit een gecachete
  lijst, subcategorieën worden na elke zoekopdracht bijgewerkt en tonen hoeveel
  advertenties erin staan.
- **Regio** — alleen een postcode (`8022RT`, `8022 RT` of `8022`). Een
  plaatsnaam of provincie wordt door Marktplaats genegeerd, dus daar
  waarschuwt de app voor.
- **Afstand** — werkt alleen samen met een postcode; 0 km betekent geen filter.
- **Max. prijs** — advertenties met "Bieden" of "Op aanvraag" hebben geen bedrag
  en blijven staan.
- **Alleen gratis advertenties** — alleen advertenties met prijstype "Gratis".
- **Promotie-advertenties verbergen (Dagtopper)** — betaalde promoties krijgen
  elke dag opnieuw de datum van vandaag en vullen daardoor de sortering op
  nieuwste. Bij een zoekterm als "racefiets" zijn 29 van de eerste 30
  resultaten promotie. Dit filter staat daarom standaard aan. Bij zeer brede
  termen als "auto" blijft er na filteren weinig over; maak de zoekterm dan
  specifieker of kies een categorie.

## Meldingen

Nieuwe advertenties komen in de Meldingen-tab en, als Telegram aanstaat, als
bericht. Het versturen gebeurt op de achtergrond met anderhalve seconde ertussen;
komen er in één ronde meer dan tien nieuwe advertenties binnen, dan gaat de rest
als één samenvatting mee.

De eerste scan van een zoekopdracht stuurt bewust niets: wat er op dat moment
staat, is niet zojuist geplaatst. Gezien-advertenties worden bewaard in
`data/seen_ids.json`, dus ook na een herstart krijg je alleen wat er echt bij
komt. Elke combinatie van zoekterm, categorie en promotie-instelling heeft een
eigen geschiedenis.

## Telegram instellen

1. Praat in Telegram met [@BotFather](https://t.me/BotFather) en stuur `/newbot`.
   Je krijgt een token in de vorm `123456789:AAE...` — neem die in één stuk over.
2. Zoek je eigen bot op in Telegram en stuur hem `/start`. Zonder dat mag de bot
   jou geen berichten sturen.
3. Vul de token in op het tabblad Telegram en klik op **Chat ID ophalen**. De app
   leest het ID uit de berichten die de bot net van je heeft gekregen.
4. Klik op **Telegram test**. Bij succes zie je de naam van je bot.

De testknop controleert eerst de token en daarna pas het chat ID, zodat een fout
aanwijst welk veld niet klopt:

| Melding van Telegram | Wat er aan de hand is |
| --- | --- |
| 404 Not Found | De token heeft niet de vorm van een token: leeg, half geplakt of met een spatie |
| 401 Unauthorized | De vorm klopt, maar de bot bestaat niet meer; vraag `/token` bij BotFather |
| chat not found | Het chat ID klopt niet |
| 403 Forbidden | Je hebt de bot nog geen `/start` gestuurd |

## Verzoeken beperken

Marktplaats publiceert geen limiet en stuurt geen rate-limit-headers, dus de app
houdt uit zichzelf afstand:

- minstens 5 seconden tussen twee verzoeken, wat de app ook vraagt;
- interval minimaal 30 seconden, met 20% spreiding zodat er geen vast ritme ontstaat;
- **adaptieve interval** — bij stilte loopt de wachttijd op (×1,5 tot het
  ingestelde maximum) en bij een nieuwe advertentie staat hij meteen weer op de
  ingestelde snelheid;
- **nachtpauze** — in de ingestelde uren wordt er niets opgevraagd;
- bij HTTP 403 of 429 stopt de monitor meteen in plaats van door te proberen.

Met een interval van 60 seconden, adaptief tot 5 minuten en een nachtpauze van
00:00 tot 07:00 komt dat neer op ongeveer 300 verzoeken per dag in plaats van
1440, met een vertraging overdag van een paar minuten.

Voor het opsporen van nieuwe advertenties is een laag aantal resultaten het
beste: de nieuwste staan vooraan, dus 30 tot 100 volstaat. Boven de 100 doet de
app een eenmalige bulk-scan in plaats van te blijven monitoren.

Elk verzoek haalt een volle pagina van 100 op, ook als er minder nodig is. Eén
groot verzoek kost namelijk evenveel wachttijd als een klein, dus zo blijft het
aantal ronden laag: met een streng filter scheelde dat acht ronden van vijf
seconden tegenover één.

## Afbeeldingen

De foto bij een advertentie komt van images.marktplaats.com, een aparte CDN die
losstaat van de zoek-API. Die telt dus niet mee voor het aantal zoekverzoeken.
In de lijst staat een miniatuur van ongeveer 2 kB, in de preview een grotere
versie van ongeveer 56 kB. Alles wordt gecachet in `data/image_cache/`, zodat
een refresh of een herstart niets opnieuw ophaalt. Uit te zetten via
"Afbeeldingen tonen" op het tabblad Weergave.

## Doorgeven aan iemand anders

Zie `packaging/README.md` (of `packaging/README.en.md`). Daar staat hoe je een bundel maakt voor Linux,
Windows en macOS waarin Python en Qt al zitten, zodat de ontvanger niets hoeft
te installeren. Bouwen kan alleen op het doelsysteem zelf; de meegeleverde
GitHub Actions-workflow doet alle drie tegelijk.

Werkt iets niet op de computer van de ontvanger, dan geeft `--selftest` per
onderdeel aan waar het misgaat.

## Bestanden

```
main.py                 startpunt
core/monitor.py         zoeken, filteren en bijhouden wat al gezien is
core/categories.py      categorielijst met cache op schijf
core/telegram_client.py versturen van een Telegram-bericht
core/secrets.py         token in de sleutelbos van het systeem
core/images.py          advertentiefoto's ophalen en cachen
core/paths.py           waar gegevens bewaard worden
core/appinfo.py         naam en versie
core/selftest.py        doorlichten met --selftest
core/saved_lists.py     opgeslagen lijsten als JSON en TXT
core/settings_manager.py zoekprofielen
core/translations.py    Nederlandse en Engelse teksten
ui/main_window.py       het venster
ui/dialogs.py           zoekprofiel- en uiterlijkvenster
ui/theme.py             kleuren, lettergrootte en stylesheet
data/seen_ids.json      welke advertenties al voorbij zijn gekomen
data/categories.json    gecachete categorielijst
data/saved_lists/       opgeslagen lijsten
data/image_cache/       gecachete advertentiefoto's
```

Instellingen staan in QSettings (op Linux onder `~/.config/PerplexityLocal/`).

De Telegram-token staat daar bewust niet bij: die gaat naar de sleutelbos van
het systeem (GNOME Keyring of KWallet op Linux, Keychain op macOS, Credential
Manager op Windows). Stond er nog een token in het instellingenbestand van een
oudere versie, dan verhuist die bij de eerste start en wordt de leesbare kopie
verwijderd. Is er geen sleutelbos beschikbaar, dan valt de app terug op
QSettings en zegt dat in de log, zodat je weet dat de token dan leesbaar op
schijf staat. Het chat-ID blijft gewoon in QSettings staan: dat is een
adres, geen sleutel.

In het venster staat de token als sterretjes met alleen de laatste vier tekens
zichtbaar, zodat je wel kunt zien welke token er staat maar niemand hem van je
scherm kan overschrijven. Klik in het veld om hem te bewerken.

De sleutelbos beschermt tegen meelezen, back-ups en per ongeluk delen. Het
beschermt niet tegen software die al onder jouw eigen account draait, want die
mag de sleutelbos net zo goed openen.

## Licentie

MIT, zie [LICENSE](LICENSE).

`data/seen_ids.json` verwijderen betekent dat de volgende scan weer een eerste
scan is: die stuurt geen meldingen en onthoudt alles opnieuw.
