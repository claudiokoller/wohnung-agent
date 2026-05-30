import html
import urllib.parse

import requests


_TELEGRAM_LIMIT = 4096  # Telegram-Zeichenlimit pro Nachricht


def _truncate(text: str, limit: int = _TELEGRAM_LIMIT) -> str:
    """Kürzt Text auf Telegram-Limit. Fügt Hinweis ans Ende."""
    if len(text) <= limit:
        return text
    cutoff = limit - 30
    return text[:cutoff] + "\n\n<i>[Text gekürzt]</i>"


def _post(token, chat_id, text, reply_markup=None):
    if not chat_id or chat_id.startswith(("DEIN_", "KOLLEGE_")):
        return  # Platzhalter noch nicht ersetzt -> überspringen
    payload = {
        "chat_id": chat_id,
        "text": _truncate(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"Telegram {chat_id}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"Telegram-Fehler an {chat_id}: {e}")


def send(token, chat_ids, listing):
    text = listing.telegram_text()
    lid = listing.id
    keyboard = {"inline_keyboard": [
        [
            {"text": "⭐ Merken",   "callback_data": f"merk_{lid}"},
            {"text": "📝 Entwurf", "callback_data": f"bewirb_{lid}"},
        ],
    ]}
    for chat_id in chat_ids:
        _post(token, chat_id, text, reply_markup=keyboard)


def send_system(token: str, chat_ids: list, text: str):
    """System-Hinweis (Fehler, Heartbeat) an alle Chat-IDs."""
    for chat_id in chat_ids:
        _post(token, chat_id, text)


def reply(token: str, chat_id: str, text: str):
    """Einzelne Antwort an einen Chat (für den Command-Layer)."""
    _post(token, chat_id, text)


def send_blank(token, chat_ids, subject, body):
    """Leere Bewerbungsstruktur zum manuellen Ausfüllen."""
    msg = (
        "✏️ <b>Manuell schreiben</b>\n"
        f"<b>Betreff:</b> {html.escape(subject)}\n\n"
        f"<pre>{html.escape(body)}</pre>"
    )
    for chat_id in chat_ids:
        _post(token, chat_id, msg)


def send_gmail_button(token: str, chat_ids: list, subject: str, body: str):
    """Sendet einen mailto:-Link der die Gmail-App mit vorausgefülltem Entwurf öffnet."""
    mailto = "mailto:?subject=" + urllib.parse.quote(subject) + "&body=" + urllib.parse.quote(body)
    text = (
        f'✉️ <a href="{mailto}">In Gmail öffnen</a> '
        f"— Empfänger aus dem Portal-Kontaktformular ergänzen."
    )
    for chat_id in chat_ids:
        _post(token, chat_id, text)


def send_draft(token, chat_ids, subject, body):
    """Bewerbungs-Entwurf als kopierfreundliche Nachricht (Monospace-Block).
    Auf dem Handy lange drücken -> kopieren -> ins Portalformular einfügen."""
    msg = (
        "📝 <b>Bewerbungs-Entwurf</b>\n"
        f"<b>Betreff:</b> {html.escape(subject)}\n\n"
        f"<pre>{html.escape(body)}</pre>"
    )
    for chat_id in chat_ids:
        _post(token, chat_id, msg)
