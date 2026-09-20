"""Sending messages through a Telegram bot.

Telegram's error messages are terse and not always clear. The difference between
404 and 401 is especially confusing: 404 means the token is not even shaped like
a token (empty, half-pasted, or containing a space), while 401 means the shape is
right but the bot does not exist or has been revoked. `describe_error` turns that
into something actionable.
"""

import re

import requests

from core.translations import tr

API_URL = "https://api.telegram.org/bot{token}/{method}"

# As BotFather hands it out: digits, colon, then letters/digits/_/-
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
    """Check the token only, without sending a message."""
    return _call(token, "getMe", timeout=timeout)


def send_telegram_message(token, chat_id, text, timeout=20):
    return _call(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout)


def describe_error(payload):
    """Turn a Telegram response into a usable explanation."""
    code = (payload or {}).get("error_code")
    description = str((payload or {}).get("description", "")).lower()

    if code == 404:
        return tr("tg_err_404")
    if code == 401:
        return tr("tg_err_401")
    if "chat not found" in description:
        return tr("tg_err_chat")
    if code == 403 or "blocked" in description:
        return tr("tg_err_blocked")
    if code == 400 and "chat_id" in description:
        return tr("tg_err_chatid")

    return str((payload or {}).get("description") or payload)


def find_chat_ids(token, timeout=20):
    """Collect chat IDs from the bot's recent messages.

    Only works if you sent the bot a message shortly beforehand; Telegram keeps
    those updates for a limited time only.
    """
    ok, payload = _call(token, "getUpdates", timeout=timeout)
    if not ok:
        return False, payload, []

    found = []
    for update in payload.get("result", []):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if not chat:
                continue
            name = chat.get("title") or " ".join(
                filter(None, [chat.get("first_name"), chat.get("last_name")])
            ) or chat.get("username") or str(chat.get("id"))
            pair = (str(chat.get("id")), name)
            if pair not in found:
                found.append(pair)
    return True, payload, found
