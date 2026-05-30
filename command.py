"""
Telegram Command-Layer.

Läuft als separater Prozess parallel zu «main.py --loop».
Long-Polling via getUpdates, kein Webhook nötig.

Start:
    python command.py

Befehle (nur aus whitelisted Chat-IDs in config.TELEGRAM_CHAT_IDS):

  Filter:
    /preis 2500          Maximalpreis CHF
    /zimmer 2.5-4        Zimmer-Range (min-max)
    /plz 8001,8004       PLZ-Filter setzen (leer = kein Filter)
    /plz add 8953        PLZ hinzufügen
    /plz del 8957        PLZ entfernen
    /plz                 Aktuelle PLZ-Liste anzeigen
    /exclude keller,studio  Ausschluss-Keywords (kommagetrennt)
    /exclude             Ausschluss-Keywords leeren
    /pause               Versand pausieren
    /resume              Versand fortsetzen
    /status              Aktuelle Filter-Einstellungen
    /now                 Sofortiger Pipeline-Durchlauf

  Inserate (per ID wie «flatfox-998877» oder PLZ wie «8001»):
    /weg <id>            Als erledigt/uninteressant markieren

  Info:
    /portale             Integrierte Quellen anzeigen

  Hilfe:
    /help                Diese Liste
"""

import re
import time

import requests

import config
import db
import notify
from application import build_blank_letter, build_letter
from sources import Listing

# Timeout für Telegram Long-Polling (Sekunden)
_POLL_TIMEOUT = 30

# Whitelist einmalig bauen statt bei jedem Update neu
_WHITELIST: set[str] = {str(cid) for cid in config.TELEGRAM_CHAT_IDS}


# ---------------------------------------------------------------------------
# Befehls-Parser (pure functions, gut testbar ohne Telegram)
# ---------------------------------------------------------------------------

def parse_preis(args: str) -> dict | None:
    """'/preis 2500' -> {'max_price': 2500.0}
    Akzeptiert auch «2'500» (Schweizer Tausend-Trennzeichen)."""
    try:
        val = float(args.strip().replace("'", "").replace(" ", ""))
        if val <= 0:
            return None
        return {"max_price": val}
    except ValueError:
        return None


def parse_zimmer(args: str) -> dict | None:
    """'/zimmer 2.5-4' -> {'min_rooms': 2.5, 'max_rooms': 4.0}
    '/zimmer 3'       -> {'min_rooms': 3.0, 'max_rooms': 3.0}"""
    args = args.strip().replace(",", ".")
    parts = re.split(r"[-–]", args, maxsplit=1)
    try:
        if len(parts) == 2:
            lo, hi = float(parts[0].strip()), float(parts[1].strip())
            if lo > hi:
                return None
            return {"min_rooms": lo, "max_rooms": hi}
        val = float(parts[0].strip())
        return {"min_rooms": val, "max_rooms": val}
    except ValueError:
        return None


def parse_plz(args: str) -> dict | None:
    """Parst /plz-Argumente. Gibt ein Dict mit 'action' zurück:
      ""             -> {"action": "show"}
      "8001,8004"    -> {"action": "set",  "plz_list": ["8001","8004"]}
      "add 8953"     -> {"action": "add",  "plz": "8953"}
      "del 8957"     -> {"action": "del",  "plz": "8957"}
      Ungültig       -> None
    """
    args = args.strip()

    if not args:
        return {"action": "show"}

    low = args.lower()

    if low.startswith("add "):
        plz = args[4:].strip()
        if re.match(r"^\d{4}$", plz):
            return {"action": "add", "plz": plz}
        return None

    if low.startswith("del "):
        plz = args[4:].strip()
        if re.match(r"^\d{4}$", plz):
            return {"action": "del", "plz": plz}
        return None

    # Direktes Setzen: kommagetrennte PLZ-Liste
    candidates = [p.strip() for p in args.split(",")]
    plz_list = [p for p in candidates if re.match(r"^\d{4}$", p)]
    if plz_list and len(plz_list) == len(candidates):
        return {"action": "set", "plz_list": plz_list}

    return None


def parse_exclude(args: str) -> dict | None:
    """'/exclude studio,keller' -> {'exclude_kw': ['studio', 'keller']}
    '/exclude'                 -> {'exclude_kw': []}  (Filter leeren)"""
    kws = [k.strip() for k in args.split(",") if k.strip()]
    return {"exclude_kw": kws}


# ---------------------------------------------------------------------------
# Antwort-Texte
# ---------------------------------------------------------------------------

def _status_text() -> str:
    state = db.get_filter_state(config.DB_PATH)
    if not state:
        return "⚠️ Filter-State nicht initialisiert."

    max_price = state.get("max_price")
    min_rooms = state.get("min_rooms")
    max_rooms = state.get("max_rooms")

    price_str = f"CHF {max_price:,.0f}".replace(",", "'") if max_price else "kein Limit"
    rooms_str = (
        f"{min_rooms}–{max_rooms}" if (min_rooms and max_rooms)
        else str(min_rooms or max_rooms or "—")
    )
    plz_str   = ", ".join(state.get("plz_list", [])) or "kein Filter"
    excl_str  = ", ".join(state.get("exclude_kw", [])) or "—"
    pause_str = "⏸ <b>PAUSIERT</b>" if state.get("paused") else "▶️ aktiv"

    return (
        f"📊 <b>Filter-Status</b> — {pause_str}\n"
        f"💰 Maximalpreis: {price_str}\n"
        f"🚪 Zimmer: {rooms_str}\n"
        f"📍 PLZ: {plz_str}\n"
        f"🚫 Exclude: {excl_str}"
    )


def _help_text() -> str:
    return (
        "🤖 <b>Wohnungs-Bot Befehle</b>\n\n"
        "<b>Filter:</b>\n"
        "/preis 2500 — Maximalpreis CHF\n"
        "/zimmer 2.5-4 — Zimmer-Range\n"
        "/plz 8001,8004 — PLZ-Filter setzen\n"
        "/plz add 8953 — PLZ hinzufügen\n"
        "/plz del 8957 — PLZ entfernen\n"
        "/plz — aktuelle PLZ-Liste\n"
        "/exclude studio,keller — Ausschluss-Keywords\n"
        "/exclude — Keywords leeren\n"
        "/pause / /resume — Versand pausieren/fortsetzen\n"
        "/status — aktuelle Einstellungen\n"
        "/now — sofortiger Durchlauf\n\n"
        "<b>Inserate</b> (ID oder PLZ):\n"
        "/liste — alle interessanten Inserate\n"
        "/weg &lt;id&gt; — als erledigt markieren\n\n"
        "<b>Info:</b>\n"
        "/portale — integrierte Quellen anzeigen\n\n"
        "<i>Beispiele: /info flatfox-12345  /merk 8001  /weg 8400</i>"
    )


# ---------------------------------------------------------------------------
# Command-Handler
# ---------------------------------------------------------------------------

def _reply(chat_id: str, text: str):
    notify.reply(config.TELEGRAM_BOT_TOKEN, chat_id, text)


def _answer_callback(callback_id: str, text: str = ""):
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def handle_callback(chat_id: str, callback_id: str, data: str):
    """Verarbeitet Inline-Button-Klicks."""
    if data.startswith("merk_"):
        lid = data[5:]
        ok = db.mark_listing(config.DB_PATH, lid, "interesting")
        if ok:
            _answer_callback(callback_id, "⭐ Gemerkt!")
            _reply(chat_id, f"⭐ Als interessant markiert: {lid}")
        else:
            _answer_callback(callback_id, "❓ Nicht gefunden")

    elif data.startswith("weg_"):
        lid = data[4:]
        ok = db.mark_listing(config.DB_PATH, lid, "done")
        if ok:
            _answer_callback(callback_id, "✅ Erledigt!")
            _reply(chat_id, f"✅ Als erledigt markiert: {lid}")
        else:
            _answer_callback(callback_id, "❓ Nicht gefunden")

    elif data.startswith("bewirb_"):
        lid = data[7:]
        row = db.get_listing(config.DB_PATH, lid)
        if not row:
            _answer_callback(callback_id, "❓ Inserat nicht gefunden")
            return
        l = _listing_from_row(row)
        try:
            subject, body     = build_letter(l, config)
            subject_b, body_b = build_blank_letter(l, config)
            notify.send_draft(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject, body)
            notify.send_blank(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject_b, body_b)
            notify.send_gmail_button(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject, body)
            _answer_callback(callback_id, "📝 Entwurf gesendet!")
        except Exception as e:
            _answer_callback(callback_id, "❌ Fehler")
            _reply(chat_id, f"❌ Fehler beim Entwurf: {e}")

    else:
        _answer_callback(callback_id)


def _listing_from_row(row: dict) -> Listing:
    return Listing(
        id       = row["id"],
        source   = row["source"],
        title    = row["title"]    or "Wohnung",
        price    = row["price"]    or "?",
        rooms    = row["rooms"]    or "?",
        space    = row["space"]    or "?",
        location = row["location"] or "—",
        url      = row["url"]      or "",
    )


def handle_command(chat_id: str, text: str):
    """Verarbeitet einen Telegram-Befehl aus einem whitelisted Chat."""
    # /befehl@BotName -> Bot-Mention entfernen
    parts = text.strip().split(None, 1)
    cmd_raw = parts[0].lstrip("/").lower()
    cmd     = cmd_raw.split("@")[0]
    args    = parts[1].strip() if len(parts) > 1 else ""

    # --- /liste ---
    if cmd == "liste":
        rows = db.get_interesting_listings(config.DB_PATH)
        if not rows:
            _reply(chat_id, "⭐ Keine als interessant markierten Inserate.")
            return
        total = len(rows)
        shown = rows[:10]
        header = f"⭐ <b>Interessante Inserate ({total})</b>"
        if total > 10:
            header += f" — zeige 10 von {total}"
        lines = [header + "\n"]
        for r in shown:
            lines.append(
                f"• <b>{r['id']}</b>\n"
                f"  📍 {r['location'] or '—'} · 💰 {r['price'] or '?'} · 🚪 {r['rooms'] or '?'} Zi\n"
                f"  🔗 {r['url']}"
            )
        _reply(chat_id, "\n".join(lines))

    # --- /help ---
    elif cmd == "help":
        _reply(chat_id, _help_text())

    # --- /status ---
    elif cmd == "status":
        _reply(chat_id, _status_text())

    # --- /preis ---
    elif cmd == "preis":
        result = parse_preis(args)
        if result is None:
            _reply(chat_id, "⚠️ Ungültig. Beispiel: /preis 2500")
            return
        db.set_filter_state(config.DB_PATH, **result)
        price_fmt = f"{result['max_price']:,.0f}".replace(",", "'")
        _reply(chat_id, f"✅ Maximalpreis auf CHF {price_fmt} gesetzt.")

    # --- /zimmer ---
    elif cmd == "zimmer":
        result = parse_zimmer(args)
        if result is None:
            _reply(chat_id, "⚠️ Ungültig. Beispiel: /zimmer 2.5-4")
            return
        db.set_filter_state(config.DB_PATH, **result)
        _reply(chat_id, f"✅ Zimmer-Range: {result['min_rooms']}–{result['max_rooms']}")

    # --- /plz ---
    elif cmd == "plz":
        result = parse_plz(args)
        if result is None:
            _reply(chat_id, "⚠️ Ungültig. Beispiele: /plz 8001,8004  /plz add 8953  /plz del 8957")
            return

        state   = db.get_filter_state(config.DB_PATH)
        current = list(state.get("plz_list", []))
        action  = result["action"]

        if action == "show":
            msg = f"📍 PLZ-Liste: {', '.join(current)}" if current else "📍 PLZ-Filter ist leer (kein Filter aktiv)."
            _reply(chat_id, msg)

        elif action == "add":
            plz = result["plz"]
            if plz not in current:
                current.append(plz)
            db.set_filter_state(config.DB_PATH, plz_list=current)
            _reply(chat_id, f"✅ PLZ {plz} hinzugefügt. Aktuelle Liste: {', '.join(current)}")

        elif action == "del":
            plz = result["plz"]
            current = [p for p in current if p != plz]
            db.set_filter_state(config.DB_PATH, plz_list=current)
            msg = f"✅ PLZ {plz} entfernt. Aktuelle Liste: {', '.join(current) or '(leer = kein Filter)'}"
            _reply(chat_id, msg)

        elif action == "set":
            new_list = result["plz_list"]
            db.set_filter_state(config.DB_PATH, plz_list=new_list)
            _reply(chat_id, f"✅ PLZ-Filter: {', '.join(new_list)}")

    # --- /exclude ---
    elif cmd == "exclude":
        result = parse_exclude(args)
        db.set_filter_state(config.DB_PATH, **result)
        kws = result["exclude_kw"]
        if kws:
            _reply(chat_id, f"✅ Ausschluss-Keywords: {', '.join(kws)}")
        else:
            _reply(chat_id, "✅ Ausschluss-Keywords geleert.")

    # --- /pause ---
    elif cmd == "pause":
        db.set_filter_state(config.DB_PATH, paused=True)
        _reply(chat_id, "⏸ Bot pausiert. Neue Inserate werden nicht gesendet.")

    # --- /resume ---
    elif cmd == "resume":
        db.set_filter_state(config.DB_PATH, paused=False)
        _reply(chat_id, "▶️ Bot wieder aktiv.")

    # --- /now ---
    elif cmd == "now":
        _reply(chat_id, "🔄 Pipeline läuft…")
        try:
            # Import hier um zirkuläre Abhängigkeiten zu vermeiden
            from main import run_once
            run_once()
            _reply(chat_id, "✅ Durchlauf abgeschlossen.")
        except Exception as e:
            _reply(chat_id, f"❌ Fehler beim Durchlauf: {e}")

    # --- /weg ---
    elif cmd == "weg":
        if not args:
            _reply(chat_id, "⚠️ Verwendung: /weg <id>  z.B. /weg homegate-3456789")
            return
        if re.match(r"^\d{4}$", args):
            _reply(chat_id, "⚠️ Bitte Inserat-ID angeben (z.B. homegate-3456789), nicht PLZ.")
            return
        ok = db.mark_listing(config.DB_PATH, args, "done")
        if ok:
            _reply(chat_id, f"✅ Als erledigt markiert: {args}")
        else:
            _reply(chat_id, f"❓ Kein Inserat gefunden für «{args}».")

    # --- /portale ---
    elif cmd == "portale":
        from email_source import ALERT_SENDER_DOMAINS, LISTING_PATTERNS
        portale = list(LISTING_PATTERNS.keys())
        lines = [f"• {p}" for p in portale]
        lines.append(f"\n<i>Alert-Mails werden von folgenden Domains akzeptiert:</i>")
        lines += [f"  {d}" for d in ALERT_SENDER_DOMAINS]
        _reply(chat_id, "📡 <b>Integrierte Portale</b>\n\n" + "\n".join(lines))

    # --- Unbekannt ---
    else:
        _reply(chat_id, "❓ Unbekannter Befehl. /help für die Befehlsliste.")


# ---------------------------------------------------------------------------
# Long-Polling Loop
# ---------------------------------------------------------------------------

def _get_updates(offset: int) -> list:
    """Holt neue Telegram-Updates via Long-Polling."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": _POLL_TIMEOUT},
            timeout=_POLL_TIMEOUT + 10,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except requests.exceptions.ReadTimeout:
        return []  # Normaler Long-Poll-Timeout, kein Fehler
    except Exception as e:
        print(f"getUpdates fehlgeschlagen: {e}")
        time.sleep(5)
        return []


def _drain_pending_updates() -> int:
    """Liest alle ausstehenden Updates einmalig aus und gibt den nächsten Offset zurück.
    Verhindert dass nach einem Neustart alte Befehle nochmal ausgeführt werden."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"timeout": 0, "limit": 100},
            timeout=10,
        )
        results = r.json().get("result", [])
        if results:
            latest = results[-1]["update_id"] + 1
            print(f"Alte Updates übersprungen (offset={latest}).")
            return latest
    except Exception as e:
        print(f"Drain fehlgeschlagen: {e}")
    return 0


def run():
    db.init(config.DB_PATH)
    db.init_filter_state(config.DB_PATH, config.SEARCH)
    print("Command-Layer gestartet. Warte auf Telegram-Befehle…")
    print(f"Whitelist: {config.TELEGRAM_CHAT_IDS}")

    # Ausstehende Updates verwerfen — verhindert Replay alter Befehle nach Neustart
    offset = _drain_pending_updates()

    while True:
        updates = _get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1

            # Inline-Button-Klick
            callback = update.get("callback_query")
            if callback:
                cb_chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
                if cb_chat_id in _WHITELIST:
                    print(f"[btn] {cb_chat_id}: {callback.get('data', '')[:60]}")
                    try:
                        handle_callback(cb_chat_id, callback["id"], callback.get("data", ""))
                    except Exception as e:
                        print(f"Fehler bei Callback: {e}")
                        _answer_callback(callback["id"])
                continue

            # Normale Nachricht oder bearbeitete Nachricht
            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            chat_id = str(message.get("chat", {}).get("id", ""))
            text    = (message.get("text") or "").strip()

            # Whitelist: nur bekannte Chat-IDs
            if chat_id not in _WHITELIST:
                continue

            # Nur Befehle (beginnen mit /)
            if not text.startswith("/"):
                continue

            print(f"[cmd] {chat_id}: {text[:100]}")
            try:
                handle_command(chat_id, text)
            except Exception as e:
                print(f"Fehler bei Befehl «{text[:60]}»: {e}")
                try:
                    _reply(chat_id, f"❌ Interner Fehler: {e}")
                except Exception:
                    pass


if __name__ == "__main__":
    run()
