# MIAW Marktplaats Monitor v4.0

*[Nederlands](README.md) · English*

Desktop app (PyQt6) that watches Marktplaats and sends you a Telegram message as
soon as a new listing appears that matches your search.

The interface is available in Dutch and English — switch under **Taal / Language**
in the menu bar, or on the View tab.

## Getting started

On Linux:

```bash
./start.sh
```

The script creates the virtualenv if needed, installs the requirements and
starts the app. Separate scripts for Windows and macOS live in `scripts/`.

By hand also works:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

If PyQt6 complains about the `xcb` platform, a system library is missing:

```bash
sudo apt install libxcb-cursor0
```

## How the search works

The app uses Marktplaats' internal search API (`/lrp/api/search`) rather than the
ordinary search page. That is necessary because the HTML page ignores every
search argument: sorting, maximum price, distance and result count have no
effect there, and without date sorting a fresh listing often does not even make
it into the first thirty results.

Through the API these do work:

| Setting | How it is passed |
| --- | --- |
| Sorting | `sortBy=SORT_INDEX&sortOrder=DECREASING`, newest first |
| Max price | `attributeRanges[]=PriceCents:0:N` |
| Region + distance | `postcode` together with `distanceMeters` |
| Category | `l1CategoryId`, subcategory via `l2CategoryIds[]` |
| Result count | at most 100 per request, more via `offset` |

If the API is unreachable, the app falls back to reading the `__NEXT_DATA__`
block from the ordinary search page. That yields the same data, but unfiltered
and capped at thirty results.

### Please note

Marktplaats' `robots.txt` excludes `/lrp/api/search` from automated traffic. The
ordinary search page is allowed, but cannot sort by date and is therefore
unsuitable for this purpose. The app deliberately stays well below normal
browsing (see below), but that does not make the use permitted. That trade-off
is yours to make.

## Searching and filtering

- **Search term** — free text, just like on the site.
- **Category and subcategory** — main categories come from a cached list;
  subcategories are refreshed after every search and show how many listings each
  one holds.
- **Region** — a postcode only (`8022RT`, `8022 RT` or `8022`). A city or
  province name is ignored by Marktplaats, so the app warns you about it.
- **Distance** — only works together with a postcode; 0 km means no filter.
- **Max price** — listings marked "Bieden" or "Op aanvraag" carry no amount and
  are kept.
- **Free listings only** — only listings with price type "Gratis".
- **Hide promoted listings (Dagtopper)** — paid promotions get today's date
  again every day and therefore fill up the newest-first ordering. For a search
  like "racefiets", 29 of the first 30 results are promotions. That is why this
  filter is on by default. For very broad terms such as "auto" little remains
  after filtering; narrow the search or pick a category instead.

## Setting up Telegram

1. Talk to [@BotFather](https://t.me/BotFather) in Telegram and send `/newbot`.
   You get a token shaped like `123456789:AAE...` — copy it in one piece.
2. Find your own bot in Telegram and send it `/start`. Without that the bot is
   not allowed to message you.
3. Enter the token on the Telegram tab and click **Fetch chat ID**. The app reads
   the ID from the messages your bot just received.
4. Click **Test Telegram**. On success you see your bot's name.

The test button checks the token first and the chat ID second, so a failure
points at the field that is wrong:

| Telegram says | What is going on |
| --- | --- |
| 404 Not Found | The token is not shaped like a token: empty, half-pasted or containing a space |
| 401 Unauthorized | The shape is right, but the bot no longer exists; request `/token` from BotFather |
| chat not found | The chat ID is wrong |
| 403 Forbidden | You have not sent the bot `/start` yet |

## Notifications

New listings appear on the Notifications tab and, when Telegram is enabled, as a
message. Sending happens in the background with one and a half seconds between
messages; if more than ten new listings arrive in one round, the rest goes out as
a single summary.

The first scan of a search deliberately sends nothing: whatever is listed at that
moment was not just posted. Seen listings are kept in `seen_ids.json`, so even
after a restart you only get what is genuinely new. Every combination of search
term, category and promotion setting keeps its own history.

## Limiting requests

Marktplaats publishes no limit and sends no rate-limit headers, so the app keeps
its distance on its own:

- at least 5 seconds between two requests, whatever the app asks for;
- interval of at least 30 seconds, with 20% jitter so no fixed rhythm emerges;
- **adaptive interval** — during quiet spells the wait grows (×1.5 up to the
  configured maximum) and on a new listing it returns to the configured speed
  immediately;
- **quiet hours** — nothing is requested during the configured hours;
- on HTTP 403 or 429 the monitor stops at once instead of retrying.

With a 60-second interval, adaptive up to 5 minutes and quiet hours from 00:00 to
07:00, that works out to roughly 300 requests a day instead of 1440, with a
daytime delay of a couple of minutes.

For spotting new listings a low result count is best: the newest are at the
front, so 30 to 100 is plenty. Above 100 the app performs a one-off bulk scan
instead of continuing to monitor.

Every request fetches a full page of 100, even when fewer are needed. One large
request costs the same waiting time as a small one, so this keeps the number of
rounds low: with a strict filter that was the difference between eight rounds of
five seconds and one.

## Images

The photo with a listing comes from images.marktplaats.com, a separate CDN that
is unrelated to the search API. It therefore does not count towards the number of
search requests. The list shows a thumbnail of roughly 2 kB, the preview a larger
version of roughly 56 kB. Everything is cached in `image_cache/`, so a refresh or
a restart fetches nothing again. Switch it off with "Show images" on the View tab.

## Passing it on to someone else

See `packaging/README.en.md`. It explains how to build a bundle for Linux,
Windows and macOS with Python and Qt already inside, so the recipient installs
nothing. Building only works on the target system itself; the included GitHub
Actions workflow does all three at once.

If something fails on the recipient's machine, `--selftest` reports per item
where it goes wrong.

## Files

```
main.py                 entry point
core/monitor.py         searching, filtering and tracking what has been seen
core/categories.py      category list with an on-disk cache
core/telegram_client.py sending a Telegram message
core/secrets.py         token in the system keyring
core/images.py          fetching and caching listing photos
core/paths.py           where data is stored
core/appinfo.py         name and version
core/selftest.py        diagnostics via --selftest
core/saved_lists.py     saved lists as JSON and TXT
core/settings_manager.py search profiles
core/translations.py    Dutch and English texts
ui/main_window.py       the window
ui/dialogs.py           search profile and appearance windows
ui/theme.py             colours, font size and stylesheet
```

Settings live in QSettings (on Linux under `~/.config/PerplexityLocal/`).

The Telegram token deliberately does not sit there: it goes into the system
keyring (GNOME Keyring or KWallet on Linux, Keychain on macOS, Credential Manager
on Windows). If an older version left a token in the settings file, it is moved
on first start and the readable copy removed. If no keyring is available the app
falls back to QSettings and says so in the log, so you know the token is then
stored readable on disk. The chat ID stays in QSettings: that is an address, not
a key.

In the window the token is shown as asterisks with only the last four characters
visible, so you can tell which token is loaded without anyone copying it off your
screen. Click the field to edit it.

The keyring protects against reading over your shoulder, backups and accidental
sharing. It does not protect against software already running as your own user,
because that may open the keyring just as well.

Deleting `seen_ids.json` means the next scan is a first scan again: it sends no
notifications and remembers everything anew.
