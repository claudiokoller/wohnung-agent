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
    /stats               Statistiken (gesehen/gemerkt/letzter Lauf)
    /now                 Sofortiger Pipeline-Durchlauf

  Inserate (per ID wie «flatfox-998877» oder PLZ wie «8001»):
    /delete <id>         Aus der Liste entfernen

  Info:
    /portale             Integrierte Quellen anzeigen

  Hilfe:
    /help                Diese Liste
"""

import datetime as dt
import html
import re
import threading
import time
from zoneinfo import ZoneInfo

import requests

import config
import db
import notify
from plz_lookup import find_gemeinde_name, find_plz, group_by_gemeinde, plz_to_gemeinde
from application import build_blank_letter, build_letter
from sources import Listing

# Timeout für Telegram Long-Polling (Sekunden)
_POLL_TIMEOUT = 30

# Guard für /now: verhindert gleichzeitige Pipeline-Läufe
_now_running = False

# Whitelist einmalig bauen statt bei jedem Update neu
_WHITELIST: set[str] = {str(cid) for cid in config.TELEGRAM_CHAT_IDS}

# Tagesübersicht: einmal täglich um 20:00 Zürich-Zeit
_SUMMARY_HOUR = 20
_last_summary_date: dt.date | None = None


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
        if re.match(r"^\d+$", plz):          # reine Zahl → exakt 4 Stellen
            return {"action": "add", "plz": plz} if re.match(r"^\d{4}$", plz) else None
        if len(plz) >= 3:                    # Gemeindename
            return {"action": "add", "plz": plz}
        return None

    if low.startswith("del "):
        plz = args[4:].strip()
        if re.match(r"^\d+$", plz):
            return {"action": "del", "plz": plz} if re.match(r"^\d{4}$", plz) else None
        if len(plz) >= 3:
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

def _maybe_send_daily_summary():
    global _last_summary_date
    now = dt.datetime.now(ZoneInfo("Europe/Zurich"))
    if now.hour != _SUMMARY_HOUR:
        return
    today = now.date()
    if _last_summary_date == today:
        return
    _last_summary_date = today
    rows  = db.get_interesting_today(config.DB_PATH)
    stats = db.count_stats(config.DB_PATH)
    notify.send_daily_summary(
        config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, stats, rows
    )
    print(f"Tagesübersicht gesendet: {len(rows)} Inserate.")


def _stats_text() -> str:
    from datetime import datetime, timezone
    stats = db.count_stats(config.DB_PATH)
    state = db.get_filter_state(config.DB_PATH)

    last_str = stats.get("last_activity")
    if last_str:
        try:
            last_dt = datetime.fromisoformat(last_str).replace(tzinfo=timezone.utc)
            mins = int((datetime.now(timezone.utc) - last_dt).total_seconds() / 60)
            if mins < 60:
                last_fmt = f"vor {mins} Min."
            elif mins < 1440:
                last_fmt = f"vor {mins // 60}h {mins % 60}min"
            else:
                last_fmt = f"vor {mins // 1440}d"
        except Exception:
            last_fmt = last_str
    else:
        last_fmt = "noch nie"

    pause_str = "⏸ pausiert" if state.get("paused") else "▶️ aktiv"
    return (
        f"📊 <b>Bot-Statistik</b>\n\n"
        f"🔍 Gesehen: {stats['total_seen']}\n"
        f"⭐ Gemerkt: {stats['interesting']}\n"
        f"📬 Beworben: {stats['beworben']}\n"
        f"🏠 Besichtigung: {stats['besichtigung']}\n"
        f"❌ Abgelehnt: {stats['abgelehnt']}\n"
        f"✅ Erledigt: {stats['done']}\n"
        f"🕐 Letzter Durchlauf: {last_fmt}\n"
        f"{pause_str}"
    )


def _status_text() -> str:
    state = db.get_filter_state(config.DB_PATH)
    if not state:
        return "⚠️ Filter-State nicht initialisiert."

    max_price = state.get("max_price")
    min_rooms = state.get("min_rooms")
    max_rooms = state.get("max_rooms")
    min_space = state.get("min_space")

    price_str = f"CHF {max_price:,.0f}".replace(",", "'") if max_price else "kein Limit"
    rooms_str = (
        f"{min_rooms}–{max_rooms}" if (min_rooms and max_rooms)
        else str(min_rooms or max_rooms or "—")
    )
    space_str = f"ab {min_space:.0f} m²" if min_space else "kein Limit"
    plz_str   = _fmt_plz(state.get("plz_list", [])) or "kein Filter"
    excl_str  = ", ".join(state.get("exclude_kw", [])) or "—"
    kw_str    = ", ".join(state.get("kw_list",    [])) or "—"
    verfstr   = state.get("verfuegbar_ab") or "kein Filter"
    pause_str = "⏸ <b>PAUSIERT</b>" if state.get("paused") else "▶️ aktiv"

    return (
        f"📊 <b>Filter-Status</b> — {pause_str}\n"
        f"💰 Maximalpreis: {price_str}\n"
        f"🚪 Zimmer: {rooms_str}\n"
        f"📐 Mindestfläche: {space_str}\n"
        f"📍 PLZ: {plz_str}\n"
        f"🚫 Exclude: {excl_str}\n"
        f"✅ Keywords: {kw_str}\n"
        f"📅 Verfügbar bis: {verfstr}"
    )


def _help_text() -> str:
    return (
        "🤖 <b>Wohnungs-Bot Befehle</b>\n\n"
        "<b>Filter:</b>\n"
        "/preis 2500 — Maximalpreis CHF\n"
        "/zimmer 2.5-4 — Zimmer-Range\n"
        "/flaeche 60 — Mindestfläche m²\n"
        "/verfuegbar 2026-09 — nur bis dieses Datum verfügbar\n"
        "/verfuegbar — Filter leeren\n"
        "/plz 8001,8004 — PLZ-Filter setzen\n"
        "/plz add 8953 — PLZ hinzufügen\n"
        "/plz del 8957 — PLZ entfernen\n"
        "/plz — aktuelle PLZ-Liste\n"
        "/keyword balkon,lift — Whitelist (mind. 1 muss vorkommen)\n"
        "/keyword — Whitelist leeren\n"
        "/exclude studio,keller — Ausschluss-Keywords\n"
        "/exclude — Keywords leeren\n"
        "/pause / /resume — Versand pausieren/fortsetzen\n"
        "/status — aktuelle Einstellungen\n"
        "/stats — Statistiken\n"
        "/now — sofortiger Durchlauf\n\n"
        "<b>Inserate</b> (ID oder PLZ):\n"
        "/offen — offene Bewerbungen + Besichtigungen\n"
        "/liste — interessante Inserate\n"
        "/info &lt;id&gt; — Details + Notiz anzeigen\n"
        "/notiz &lt;id&gt; &lt;text&gt; — Notiz speichern\n"
        "/beworben &lt;id&gt; — als beworben markieren\n"
        "/besichtigung &lt;id&gt; — Besichtigung vereinbart\n"
        "/abgelehnt &lt;id&gt; — als abgelehnt markieren\n"
        "/delete &lt;id&gt; — als erledigt markieren\n"
        "/cleanup — erledigte Inserate aus DB löschen\n\n"
        "<b>Info:</b>\n"
        "/portale — integrierte Quellen anzeigen\n\n"
        "<i>Beispiel: /beworben homegate-3456789</i>"
    )


# ---------------------------------------------------------------------------
# Command-Handler
# ---------------------------------------------------------------------------

def _reply(chat_id: str, text: str):
    notify.reply(config.TELEGRAM_BOT_TOKEN, chat_id, text)


def _fmt_plz(plz_list) -> str:
    """Formatiert eine PLZ-Liste als '8800 (Thalwil), 8810 (Horgen)', nach PLZ
    sortiert. PLZ ohne bekannten Gemeindenamen werden roh angezeigt."""
    parts = []
    for p in sorted(plz_list):
        name = plz_to_gemeinde(p)
        parts.append(f"{p} ({name})" if name else p)
    return ", ".join(parts)


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

    elif data.startswith("beworben_"):
        lid = data[9:]
        ok = db.mark_listing(config.DB_PATH, lid, "beworben")
        if ok:
            _answer_callback(callback_id, "📬 Beworben!")
            _reply(chat_id, f"📬 Als beworben markiert: {lid}")
        else:
            _answer_callback(callback_id, "❓ Nicht gefunden")

    elif data.startswith("weg_"):
        lid = data[4:]
        ok = db.mark_listing(config.DB_PATH, lid, "done")
        if ok:
            _answer_callback(callback_id, "✅ Weg!")
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
            notify.send_draft(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject, body,
                              phone=config.APPLICANT.get("phone", ""),
                              email=config.APPLICANT.get("email", ""))
            notify.send_blank(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject_b, body_b,
                              listing_url=l.url)
            if config.DRAFT_SAVE_FILES:
                from application import save_file
                save_file(l, subject, body)
            if config.DRAFT_IMAP_APPEND:
                from application import imap_append_draft
                imap_append_draft(l, subject, body, config)
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

    # --- /offen ---
    if cmd == "offen":
        rows = db.get_open_listings(config.DB_PATH)
        if not rows:
            _reply(chat_id, "📭 Keine offenen Bewerbungen oder Besichtigungen.")
            return
        icons = {"beworben": "📬", "besichtigung": "🏠"}
        lines = [f"📋 <b>Offene Bewerbungen ({len(rows)})</b>\n"]
        for r in rows:
            title = html.escape(r.get("title") or r["id"])
            url   = r.get("url") or ""
            link  = f'<a href="{url}">{title}</a>' if url else f"<b>{title}</b>"
            icon  = icons.get(r.get("marked", ""), "•")
            lines.append(
                f"{icon} {link}\n"
                f"  📍 {r.get('location') or '—'}  ·  💰 {r.get('price') or '?'}"
                + (f"\n  📝 {html.escape(r['note'])}" if r.get("note") else "")
            )
        _reply(chat_id, "\n".join(lines))

    # --- /liste ---
    elif cmd == "liste":
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
            title = html.escape(r["title"] or r["id"])
            url   = r.get("url") or ""
            link  = f'<a href="{url}">{title}</a>' if url else f"<b>{title}</b>"
            lines.append(
                f"• {link}\n"
                f"  📍 {r['location'] or '—'}  ·  💰 {r['price'] or '?'}  ·  🚪 {r['rooms'] or '?'} Zi"
            )
        _reply(chat_id, "\n".join(lines))

    # --- /help ---
    elif cmd == "help":
        _reply(chat_id, _help_text())

    # --- /status ---
    elif cmd == "status":
        _reply(chat_id, _status_text())

    # --- /stats ---
    elif cmd == "stats":
        _reply(chat_id, _stats_text())

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

    # --- /flaeche ---
    elif cmd == "flaeche":
        try:
            val = float(args.strip())
            if val <= 0:
                raise ValueError
            db.set_filter_state(config.DB_PATH, min_space=val)
            _reply(chat_id, f"✅ Mindestfläche: {val:.0f} m²")
        except ValueError:
            _reply(chat_id, "⚠️ Ungültig. Beispiel: /flaeche 60")

    # --- /verfuegbar ---
    elif cmd == "verfuegbar":
        if not args:
            db.set_filter_state(config.DB_PATH, verfuegbar_ab=None)
            _reply(chat_id, "✅ Verfügbarkeits-Filter geleert.")
            return
        if not re.match(r"^20\d{2}-(0[1-9]|1[0-2])$", args.strip()):
            _reply(chat_id, "⚠️ Format: /verfuegbar YYYY-MM  z.B. /verfuegbar 2026-09")
            return
        db.set_filter_state(config.DB_PATH, verfuegbar_ab=args.strip())
        _reply(chat_id, f"✅ Nur Inserate verfügbar bis {args.strip()} werden gezeigt.")

    # --- /keyword ---
    elif cmd == "keyword":
        kws = [k.strip() for k in args.split(",") if k.strip()]
        db.set_filter_state(config.DB_PATH, kw_list=kws)
        if kws:
            _reply(chat_id, f"✅ Keyword-Whitelist: {', '.join(kws)}\n"
                            f"<i>Nur Inserate mit mind. einem dieser Begriffe werden gesendet.</i>")
        else:
            _reply(chat_id, "✅ Keyword-Whitelist geleert (kein Filter).")

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
            if not current:
                _reply(chat_id, "📍 PLZ-Filter ist leer (kein Filter aktiv).")
            else:
                lines = [f"📍 <b>PLZ-Filter</b> ({len(current)})"]
                for p in sorted(current):
                    name = plz_to_gemeinde(p)
                    lines.append(f"• {p} ({name})" if name else f"• {p}")
                _reply(chat_id, "\n".join(lines))

        elif action == "add":
            plz = result["plz"]
            if re.match(r"^\d{4}$", plz):
                # Direkte PLZ-Eingabe
                if plz not in current:
                    current.append(plz)
                db.set_filter_state(config.DB_PATH, plz_list=current)
                _reply(chat_id, f"✅ PLZ {plz} hinzugefügt.\nAktuelle Liste: {_fmt_plz(current)}")
            else:
                # Gemeindename → PLZ-Lookup
                found = find_plz(plz)
                name  = find_gemeinde_name(plz) or plz.title()
                if not found:
                    _reply(chat_id, f"❓ Gemeinde «{plz}» nicht gefunden. Nur Kanton Zürich unterstützt.")
                    return
                added = [p for p in found if p not in current]
                current.extend(added)
                db.set_filter_state(config.DB_PATH, plz_list=current)
                _reply(chat_id,
                    f"✅ {name}: {len(added)} PLZ hinzugefügt ({', '.join(found)}).\n"
                    f"Aktuelle Liste: {_fmt_plz(current)}"
                )

        elif action == "del":
            plz = result["plz"]
            if re.match(r"^\d{4}$", plz):
                # Direkte PLZ
                current = [p for p in current if p != plz]
                db.set_filter_state(config.DB_PATH, plz_list=current)
                _reply(chat_id, f"✅ PLZ {plz} entfernt.\nAktuelle Liste: {_fmt_plz(current) or '(leer)'}")
            else:
                # Gemeindename → alle zugehörigen PLZ entfernen
                found = find_plz(plz)
                name  = find_gemeinde_name(plz) or plz.title()
                if not found:
                    _reply(chat_id, f"❓ Gemeinde «{plz}» nicht gefunden. Nur Kanton Zürich unterstützt.")
                    return
                removed = [p for p in found if p in current]
                current = [p for p in current if p not in found]
                db.set_filter_state(config.DB_PATH, plz_list=current)
                _reply(chat_id,
                    f"✅ {name}: {len(removed)} PLZ entfernt ({', '.join(removed) or '—'}).\n"
                    f"Aktuelle Liste: {_fmt_plz(current) or '(leer = kein Filter)'}"
                )

        elif action == "set":
            new_list = result["plz_list"]
            db.set_filter_state(config.DB_PATH, plz_list=new_list)
            _reply(chat_id, f"✅ PLZ-Filter gesetzt:\n{_fmt_plz(new_list)}")

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
        global _now_running
        if _now_running:
            _reply(chat_id, "⏳ Durchlauf läuft bereits, bitte warten…")
            return
        _now_running = True
        _reply(chat_id, "🔄 Pipeline läuft…")
        def _run_pipeline():
            global _now_running
            try:
                from main import run_once
                run_once()
                _reply(chat_id, "✅ Durchlauf abgeschlossen.")
            except Exception as e:
                _reply(chat_id, f"❌ Fehler beim Durchlauf: {e}")
            finally:
                _now_running = False
        threading.Thread(target=_run_pipeline, daemon=True).start()

    # --- /beworben / /besichtigung / /abgelehnt ---
    elif cmd in ("beworben", "besichtigung", "abgelehnt"):
        if not args:
            _reply(chat_id, f"⚠️ Verwendung: /{cmd} <id>  z.B. /{cmd} homegate-3456789")
            return
        icons = {"beworben": "📬", "besichtigung": "🏠", "abgelehnt": "❌"}
        labels = {"beworben": "als beworben", "besichtigung": "Besichtigung vereinbart",
                  "abgelehnt": "als abgelehnt"}
        ok = db.mark_listing(config.DB_PATH, args, cmd)
        if ok:
            _reply(chat_id, f"{icons[cmd]} {labels[cmd].capitalize()}: {args}")
        else:
            _reply(chat_id, f"❓ Kein Inserat gefunden für «{args}».")

    # --- /info ---
    elif cmd == "info":
        if not args:
            _reply(chat_id, "⚠️ Verwendung: /info <id>  z.B. /info homegate-3456789")
            return
        row = db.get_listing(config.DB_PATH, args)
        if not row:
            _reply(chat_id, f"❓ Kein Inserat gefunden für «{args}».")
            return
        status_icons = {
            "interesting": "⭐", "beworben": "📬", "besichtigung": "🏠",
            "abgelehnt": "❌", "done": "✅",
        }
        marked = row.get("marked")
        status_str = f"{status_icons.get(marked, '•')} {marked}" if marked else "—"
        note_str   = f"\n📝 <b>Notiz:</b> {html.escape(row['note'])}" if row.get("note") else ""
        url = row.get("url") or ""
        _reply(chat_id,
            f"🏠 <b>{html.escape(row.get('title') or row['id'])}</b>\n\n"
            f"📍 {row.get('location') or '—'}\n"
            f"🚪 {row.get('rooms') or '?'} Zi  ·  "
            f"📐 {row.get('space') or '?'} m²  ·  "
            f"💰 {row.get('price') or '?'}\n"
            f"📅 {row.get('available') or '—'}\n"
            f"🔖 Status: {status_str}"
            f"{note_str}\n\n"
            f"🔗 {url}\n"
            f"<i>{row['id']}</i>"
        )

    # --- /notiz ---
    elif cmd == "notiz":
        parts = args.split(None, 1)
        if len(parts) < 2:
            _reply(chat_id, "⚠️ Verwendung: /notiz <id> <text>")
            return
        lid, note_text = parts[0], parts[1]
        ok = db.set_note(config.DB_PATH, lid, note_text)
        if ok:
            _reply(chat_id, f"📝 Notiz gespeichert für {lid}.")
        else:
            _reply(chat_id, f"❓ Kein Inserat gefunden für «{lid}».")

    # --- /delete ---
    elif cmd == "delete":
        if not args:
            _reply(chat_id, "⚠️ Verwendung: /delete <id>  z.B. /delete homegate-3456789")
            return
        ok = db.mark_listing(config.DB_PATH, args, "done")
        if ok:
            _reply(chat_id, f"✅ Entfernt: {args}")
        else:
            _reply(chat_id, f"❓ Kein Inserat gefunden für «{args}».")

    # --- /cleanup ---
    elif cmd == "cleanup":
        result = db.cleanup_done(config.DB_PATH)
        n = result["listings"]
        if n == 0:
            _reply(chat_id, "🧹 Nichts zu bereinigen — keine erledigten Inserate.")
        else:
            _reply(chat_id, f"🧹 {n} erledigte Inserat{'e' if n != 1 else ''} gelöscht.")

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
        # Backstop: kein Fehler im Loop-Körper (Tagesübersicht, DB-Lock, Telegram)
        # darf den Command-Layer killen. Inner-Handler haben eigene try/except.
        try:
            _maybe_send_daily_summary()
        except Exception as e:
            print(f"Tagesübersicht-Fehler (weiter): {e}")

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
