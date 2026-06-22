import html
import time

import requests


_TELEGRAM_LIMIT = 4096  # Telegram-Zeichenlimit pro Nachricht
_SEND_ATTEMPTS  = 4     # Versuche pro Nachricht bei transienten Fehlern
_SEND_BACKOFF   = 3     # Basis-Wartezeit (s) zwischen Versuchen


def _api_call(url: str, payload: dict, chat_id: str) -> bool:
    """POST an die Telegram-API mit Retry. True = zugestellt.

    Wiederholt bei transienten Fehlern (Timeout/Verbindung, HTTP 5xx) und
    respektiert Rate-Limits (HTTP 429 -> retry_after). Permanente Fehler
    (z.B. 400/403) werden NICHT wiederholt und geben False zurück, ohne den
    Aufrufer zu blockieren."""
    for attempt in range(1, _SEND_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, timeout=15)
        except Exception as e:
            if attempt < _SEND_ATTEMPTS:
                time.sleep(_SEND_BACKOFF * attempt)
                continue
            print(f"Telegram {chat_id}: Netzfehler nach {attempt} Versuchen — {e}")
            return False

        if resp.status_code == 200:
            return True
        if resp.status_code == 429:
            # Rate-Limit: Telegram nennt die Wartezeit in parameters.retry_after
            try:
                retry_after = int(resp.json()["parameters"]["retry_after"])
            except Exception:
                retry_after = _SEND_BACKOFF * attempt
            print(f"Telegram {chat_id}: 429 Rate-Limit, warte {retry_after}s")
            time.sleep(min(retry_after, 60) + 1)
            continue
        if 500 <= resp.status_code < 600:
            if attempt < _SEND_ATTEMPTS:
                time.sleep(_SEND_BACKOFF * attempt)
                continue
            print(f"Telegram {chat_id}: {resp.status_code} (Server) nach {attempt} Versuchen")
            return False
        # Permanenter Client-Fehler (400 bad request, 403 blockiert, ...)
        print(f"Telegram {chat_id}: {resp.status_code} {resp.text[:200]} — nicht wiederholt")
        return False
    return False


def _truncate(text: str, limit: int = _TELEGRAM_LIMIT) -> str:
    """Kürzt Text auf Telegram-Limit. Fügt Hinweis ans Ende."""
    if len(text) <= limit:
        return text
    cutoff = limit - 30
    return text[:cutoff] + "\n\n<i>[Text gekürzt]</i>"


def _post_photo(token, chat_id, photo_url, caption, reply_markup=None) -> bool:
    """Sendet ein Foto mit Caption. True = zugestellt. Fällt bei einem
    permanenten Foto-Fehler (z.B. ungültige Bild-URL) auf eine Text-Nachricht
    zurück und gibt deren Ergebnis zurück."""
    if not chat_id or chat_id.startswith(("DEIN_", "KOLLEGE_")):
        return True   # Platzhalter -> nichts zu tun, nicht als Verlust werten
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": _truncate(caption, 1024),  # Telegram Caption-Limit
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if _api_call(f"https://api.telegram.org/bot{token}/sendPhoto", payload, chat_id):
        return True
    # Foto endgültig nicht zustellbar -> wenigstens den Text senden
    print(f"Telegram photo {chat_id}: Fallback auf Text")
    return _post(token, chat_id, caption, reply_markup=reply_markup)


def _post(token, chat_id, text, reply_markup=None) -> bool:
    if not chat_id or chat_id.startswith(("DEIN_", "KOLLEGE_")):
        return True  # Platzhalter noch nicht ersetzt -> überspringen, kein Verlust
    payload = {
        "chat_id": chat_id,
        "text": _truncate(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _api_call(f"https://api.telegram.org/bot{token}/sendMessage", payload, chat_id)


def send(token, chat_ids, listing) -> bool:
    """Sendet ein Inserat an alle chat_ids. True nur, wenn ALLE zugestellt
    wurden — sonst False, damit der Aufrufer (main.run_once) es nicht als
    'gesehen' markiert und der Resend-Sweep es erneut versucht."""
    text = listing.telegram_text()
    lid = listing.id
    keyboard = {"inline_keyboard": [
        [
            {"text": "⭐ Merken",    "callback_data": f"merk_{lid}"},
            {"text": "📝 Entwurf",  "callback_data": f"bewirb_{lid}"},
        ],
        [
            {"text": "📬 Beworben", "callback_data": f"beworben_{lid}"},
            {"text": "❌ Weg",      "callback_data": f"weg_{lid}"},
        ],
    ]}
    ok = True
    for chat_id in chat_ids:
        if listing.image:
            ok = _post_photo(token, chat_id, listing.image, text, reply_markup=keyboard) and ok
        else:
            ok = _post(token, chat_id, text, reply_markup=keyboard) and ok
    return ok


def send_daily_summary(token: str, chat_ids: list, stats: dict, listings: list):
    """Tagesübersicht: gemerkete Inserate + offene Bewerbungen + Statistik."""
    lines = ["📅 <b>Tagesübersicht</b>\n"]

    # Neue Inserate heute
    new_today = stats.get("new_today", 0)
    lines.append(f"🔍 Heute neu: {new_today} Inserat{'e' if new_today != 1 else ''}")

    # Offene Bewerbungen
    beworben     = stats.get("beworben", 0)
    besichtigung = stats.get("besichtigung", 0)
    if beworben or besichtigung:
        lines.append(
            f"📬 Beworben: {beworben}  ·  🏠 Besichtigung: {besichtigung}"
        )

    # Heute gemerkete Inserate
    if listings:
        n = len(listings)
        lines.append(f"\n⭐ <b>Heute gemerkt ({n})</b>")
        for r in listings:
            title = html.escape(r.get("title") or r["id"])
            url   = r.get("url") or ""
            link  = f'<a href="{url}">{title}</a>' if url else f"<b>{title}</b>"
            lines.append(
                f"• {link}\n"
                f"  📍 {r.get('location') or '—'}  ·  💰 {r.get('price') or '?'}  ·  🚪 {r.get('rooms') or '?'} Zi"
            )
    else:
        lines.append("\nHeute keine Inserate gemerkt.")

    text = "\n".join(lines)
    for chat_id in chat_ids:
        _post(token, chat_id, text)


def send_system(token: str, chat_ids: list, text: str):
    """System-Hinweis (Fehler, Heartbeat) an alle Chat-IDs."""
    for chat_id in chat_ids:
        _post(token, chat_id, text)


def send_document(token, chat_ids, file_path: str, caption: str = "") -> bool:
    """Lädt eine Datei als Dokument in alle chat_ids hoch (z.B. DB-Backup als
    Off-site-Kopie -> liegt danach in Telegrams Cloud). True nur, wenn überall
    zugestellt. Eigene Retry-Schleife wie _api_call, aber mit Multipart-Upload."""
    ok = True
    for chat_id in chat_ids:
        if not chat_id or chat_id.startswith(("DEIN_", "KOLLEGE_")):
            continue
        delivered = False
        for attempt in range(1, _SEND_ATTEMPTS + 1):
            try:
                with open(file_path, "rb") as fh:
                    resp = requests.post(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data={
                            "chat_id": chat_id,
                            "caption": _truncate(caption, 1024),
                            "parse_mode": "HTML",
                        },
                        files={"document": fh},
                        timeout=60,
                    )
            except Exception as e:
                if attempt < _SEND_ATTEMPTS:
                    time.sleep(_SEND_BACKOFF * attempt)
                    continue
                print(f"Telegram doc {chat_id}: Netzfehler nach {attempt} Versuchen — {e}")
                break
            if resp.status_code == 200:
                delivered = True
                break
            if resp.status_code == 429:
                try:
                    retry_after = int(resp.json()["parameters"]["retry_after"])
                except Exception:
                    retry_after = _SEND_BACKOFF * attempt
                time.sleep(min(retry_after, 60) + 1)
                continue
            if 500 <= resp.status_code < 600 and attempt < _SEND_ATTEMPTS:
                time.sleep(_SEND_BACKOFF * attempt)
                continue
            print(f"Telegram doc {chat_id}: {resp.status_code} {resp.text[:200]}")
            break
        ok = delivered and ok
    return ok


def reply(token: str, chat_id: str, text: str):
    """Einzelne Antwort an einen Chat (für den Command-Layer)."""
    _post(token, chat_id, text)


def send_blank(token, chat_ids, subject, body, listing_url: str = ""):
    """Leere Bewerbungsstruktur zum manuellen Ausfüllen."""
    msg = (
        "✏️ <b>Manuell schreiben</b>\n"
        f"<b>Betreff:</b> {html.escape(subject)}\n\n"
        f"<pre>{html.escape(body)}</pre>"
    )
    keyboard = None
    if listing_url:
        keyboard = {"inline_keyboard": [[{"text": "🔗 Zum Kontaktformular", "url": listing_url}]]}
    for chat_id in chat_ids:
        _post(token, chat_id, msg, reply_markup=keyboard)



def send_draft(token, chat_ids, subject, body, phone: str = "", email: str = ""):
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
    for chat_id in chat_ids:
        _post(token, chat_id, msg)
