"""Telegram-Auslieferung.

Credential-Prioritaet:
  1. config/config.json  (telegram.bot_token / telegram.chat_id)
  2. Umgebungsvariablen   (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
     TELEGRAM_HOME_CHANNEL, TELEGRAM_ALLOWED_USERS)

Die Hermes-globale .env liefert TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS
(die User-ID des Besitzers). Wenn kein explizites chat_id gesetzt ist, wird
die erste erlaubte User-ID als Ziel genutzt -> Nachrichten gehen an dich selbst.

Falls kein Token da: nur DB-Log (silent mode). So laeuft die Pipeline ohne
Telegram-Setup testbar durch.

Konfiguration (config/config.json):
    {"telegram": {"bot_token": "...", "chat_id": "..."}}
"""
import os
import requests

try:
    from paths import load_config
    from db import log_telegram
except Exception:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from paths import load_config
    from db import log_telegram


def _cfg():
    cfg = load_config().get("telegram", {})
    token = cfg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = (cfg.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
            or os.environ.get("TELEGRAM_HOME_CHANNEL")
            or os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip())
    return token, chat


def send_telegram(text, ziel=None):
    """Sendet eine Nachricht. Immer in DB loggen; nur senden wenn token da."""
    token, chat = _cfg()
    chat = ziel or chat
    log_telegram(chat or "default", text)
    if not token or not chat:
        return False  # silent mode (kein Token/Chat -> nur geloggt)
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        return r.status_code == 200
    except Exception:
        return False


def alert(text):
    """Kurzalias fuer kritische Alerts (E1 Warnung, E4 Dissens, K4 Exit ...)."""
    return send_telegram(text)


def verify_token():
    """Read-only Check: liefert Bot-Username bei gueltigem Token, sonst None.
    Macht KEINEN Outbound-Chat-Versand."""
    token, _ = _cfg()
    if not token:
        return None
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        if r.status_code == 200:
            return r.json().get("result", {}).get("username")
    except Exception:
        pass
    return None
