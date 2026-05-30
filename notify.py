import html

import requests


_TELEGRAM_LIMIT = 4096  # Telegram-Zeichenlimit pro Nachricht


def _truncate(text: str, limit: int = _TELEGRAM_LIMIT) -> str:
    """Kürzt Text auf Telegram-Limit. Fügt Hinweis ans Ende."""
    if len(text) <= limit:
        return text
    cutoff = limit - 30
    return text[:cutoff] + "\n\n<i>[Text gekürzt]</i>"


def _post_photo(token, chat_id, photo_url, caption, reply_markup=None):
    """Sendet ein Foto mit Caption. Fällt auf Text-Nachricht zurück wenn Foto fehlschlägt."""
    if not chat_id or chat_id.startswith(("DEIN_", "KOLLEGE_")):
        return
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": _truncate(caption, 1024),  # Telegram Caption-Limit
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            json=payload,
            timeout=15,
        )
        if resp.status_code == 200:
            return
        print(f"Telegram photo {chat_id}: {resp.status_code} — Fallback auf Text")
    except Exception as e:
        print(f"Telegram-Foto-Fehler an {chat_id}: {e}")
    # Fallback
    _post(token, chat_id, caption, reply_markup=reply_markup)


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
    keyboard = {"inline_keyboard": [[
        {"text": "⭐ Merken",   "callback_data": f"merk_{lid}"},
        {"text": "📝 Entwurf", "callback_data": f"bewirb_{lid}"},
    ]]}
    for chat_id in chat_ids:
        if listing.image:
            _post_photo(token, chat_id, listing.image, text, reply_markup=keyboard)
        else:
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



def send_draft(token, chat_ids, subject, body, phone: str = "", email: str = "", listing_url: str = ""):
    """Bewerbungs-Entwurf als kopierfreundliche Nachricht (Monospace-Block).
    Auf dem Handy lange drücken -> kopieren -> ins Portalformular einfügen."""
    contact = ""
    if phone or email:
        parts = []
        if phone:
            parts.append(f"📞 {html.escape(phone)}")
        if email:
            parts.append(f"✉️ {html.escape(email)}")
        contact = "  ·  ".join(parts) + "\n"
    msg = (
        f"📝 <b>Bewerbungs-Entwurf</b>\n"
        f"{contact}"
        f"<b>Betreff:</b> {html.escape(subject)}\n\n"
        f"<pre>{html.escape(body)}</pre>"
    )
    keyboard = None
    if listing_url:
        keyboard = {"inline_keyboard": [[{"text": "🔗 Zum Kontaktformular", "url": listing_url}]]}
    for chat_id in chat_ids:
        _post(token, chat_id, msg, reply_markup=keyboard)
