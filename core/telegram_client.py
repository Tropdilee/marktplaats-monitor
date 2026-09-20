"""Berichten versturen via een Telegram-bot.

De foutmeldingen van Telegram zijn kort en niet altijd duidelijk. Vooral het
verschil tussen 404 en 401 is verwarrend: 404 betekent dat de token niet eens de
vorm van een token heeft (leeg, half geplakt of met een spatie erin), terwijl
401 betekent dat de vorm klopt maar de bot niet bestaat of is ingetrokken.
`describe_error` vertaalt dat naar iets waar je wat aan hebt.
"""

import re

import requests

API_URL = "https://api.telegram.org/bot{token}/{method}"

# Zoals BotFather hem geeft: cijfers, dubbele punt, dan letters/cijfers/_/-
TOKEN_PATTERN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def looks_like_token(token):
    return bool(TOKEN_PATTERN.match((token or "").strip()))


def _call(token, method, data=None, timeout=20):
    url = API_URL.format(token=token, method=method)
    resp = requests.post(url, data=data or {}, timeout=timeout)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"ok": False, "description": resp.text[:200]}
    return resp.ok and payload.get("ok"), payload


def get_bot_info(token, timeout=20):
    """Controleer alleen de token, zonder een bericht te sturen."""
    return _call(token, "getMe", timeout=timeout)


def send_telegram_message(token, chat_id, text, timeout=20):
    return _call(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout)


def describe_error(payload):
    """Zet een Telegram-antwoord om in een bruikbare uitleg."""
    code = (payload or {}).get("error_code")
    omschrijving = str((payload or {}).get("description", "")).lower()

    if code == 404:
        return (
            "De bot token wordt niet herkend als token. Hij hoort eruit te zien "
            "als 123456789:AAE... — controleer of de hele regel uit BotFather is "
            "overgenomen, in één stuk en zonder spaties."
        )
    if code == 401:
        return (
            "De bot token heeft de juiste vorm maar wordt afgewezen. Waarschijnlijk "
            "is hij ingetrokken of vervangen. Vraag met /token bij BotFather een "
            "nieuwe op."
        )
    if "chat not found" in omschrijving:
        return (
            "Het chat ID klopt niet. Stuur je bot eerst een bericht en haal het "
            "juiste ID op via de knop 'Chat ID ophalen'."
        )
    if code == 403 or "blocked" in omschrijving:
        return (
            "De bot mag jou geen berichten sturen. Open de chat met je bot en "
            "stuur /start."
        )
    if code == 400 and "chat_id" in omschrijving:
        return "Het chat ID ontbreekt of heeft een verkeerde vorm."

    return str((payload or {}).get("description") or payload)


def find_chat_ids(token, timeout=20):
    """Haal chat-ID's op uit de recente berichten aan de bot.

    Werkt alleen als je de bot kort daarvoor zelf een bericht hebt gestuurd;
    Telegram bewaart die updates maar een beperkte tijd.
    """
    ok, payload = _call(token, "getUpdates", timeout=timeout)
    if not ok:
        return False, payload, []

    gevonden = []
    for update in payload.get("result", []):
        for sleutel in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(sleutel) or {}).get("chat")
            if not chat:
                continue
            naam = chat.get("title") or " ".join(
                filter(None, [chat.get("first_name"), chat.get("last_name")])
            ) or chat.get("username") or str(chat.get("id"))
            paar = (str(chat.get("id")), naam)
            if paar not in gevonden:
                gevonden.append(paar)
    return True, payload, gevonden
