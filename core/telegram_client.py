import requests

def send_telegram_message(token, chat_id, text, timeout=20):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=timeout)
    data = resp.json()
    return resp.ok and data.get("ok"), data