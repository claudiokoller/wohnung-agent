"""
Wohnungs-Bot Zürich.

Modi:
  python main.py                einmal laufen (gut für cron)
  python main.py --loop         Dauerschleife, alle POLL_INTERVAL_MIN Minuten
  python main.py --seed         aktuelle Inserate als "gesehen" markieren OHNE
                                zu senden -> beim Setup einmal ausführen, damit
                                ihr nicht 50 Altinserate auf einen Schlag kriegt
  python main.py --dump-emails  rohe HTML-Bodies der Portal-Mails speichern,
                                um die IMAP-Parser zu tunen (sendet nichts,
                                markiert nichts als gelesen)
  python main.py --backup       konsistentes DB-Backup + Integritätscheck

Quellen:
  - fetch_email : Suchabo-Mails von Homegate/ImmoScout24/newhome/Flatfox
                  via IMAP (ToS-konform, kein Bot-Schutz-Problem)
  Hinweis: Flatfox-API (v1/public-flat) wurde von Flatfox abgeschaltet.
           Flatfox als Suchabo-Mail einrichten, dann läuft es via fetch_email.

Filter-State:
  Beim ersten Start werden Werte aus config.SEARCH in die DB geseedet.
  Danach liest main.py den State aus der DB (command.py überschreibt ihn).
  Die Bounding Box (bbox) bleibt statisch aus config.SEARCH.
"""
import sys
import time
from datetime import datetime, timezone

import config
import db
import email_source
import notify
from email_source import dump_emails, fetch_email

# Jede Quelle: (search, cfg) -> list[Listing]
SOURCES = [fetch_email]

# Fehler-Tracking: wie oft hat eine Quelle hintereinander versagt?
_source_failures: dict[str, int] = {}
_MAX_FAILURES = 3          # ab hier Telegram-Warnung
_HEARTBEAT_HOURS = 24      # nach X Stunden ohne Aktivität warnen
_last_heartbeat_sent: datetime | None = None
_last_auth_alert: datetime | None = None   # letzte IMAP-Login-Warnung
_AUTH_ALERT_INTERVAL_H = 6                  # Login-Warnung alle X Stunden wiederholen

# Stiller Parser-/Template-Bruch: Mails kommen an, aber 0 Inserate geparst.
_zero_parse_streak = 0
_last_parse_alert: datetime | None = None
_PARSE_ALERT_INTERVAL_H = 6
_PARSE_STREAK_THRESHOLD = 3                 # erst nach X solchen Durchläufen warnen


def _is_auth_error(e: Exception) -> bool:
    """Erkennt einen abgelehnten IMAP-Login (App-Passwort tot/widerrufen)."""
    s = str(e).upper()
    return (
        "AUTHENTICATIONFAILED" in s
        or "INVALID CREDENTIALS" in s
        or "INVALID_GRANT" in s        # toter/widerrufener OAuth-Refresh-Token
        or "INVALID_CLIENT" in s       # falsche OAuth-Client-ID/-Secret
    )


def _auth_ok():
    """Login hat geklappt -> Warn-Zustand zurücksetzen."""
    global _last_auth_alert
    _last_auth_alert = None


def _auth_alert(err):
    """Sendet bei abgelehntem IMAP-Login sofort eine Telegram-Warnung und
    wiederholt sie alle _AUTH_ALERT_INTERVAL_H Stunden, bis der Login wieder
    klappt. Anders als der generische Fehlerzähler feuert das nicht nur einmal
    und überlebt so einen tagelangen Ausfall nicht unbemerkt."""
    global _last_auth_alert
    now = datetime.now(timezone.utc)
    if (_last_auth_alert
            and (now - _last_auth_alert).total_seconds() < _AUTH_ALERT_INTERVAL_H * 3600):
        return
    notify.send_system(
        config.TELEGRAM_BOT_TOKEN,
        config.TELEGRAM_CHAT_IDS,
        "🔴 <b>Mail-Login (IMAP) abgelehnt</b> — der Bot kommt nicht ins Postfach "
        "und empfängt KEINE Inserate.\n\n"
        "Zugangsdaten prüfen: in der <code>.env</code> auf dem VPS "
        "<code>WOHNUNGS_IMAP_USER</code>/<code>WOHNUNGS_IMAP_PASS</code> (mailbox.org-"
        "Passwort) korrigieren und Services neu starten "
        "(<code>systemctl restart wohnungs-bot wohnungs-bot-cmd</code>).\n\n"
        f"Fehler: {err}",
    )
    _last_auth_alert = now
    print("Auth-Warnung an Telegram gesendet.")


def _note_parser_health():
    """Erkennt einen stillen Parser-/Template-Bruch: Portal-Mails treffen ein,
    aber 0 Inserate werden geparst. Der 24h-Heartbeat greift hier NICHT, weil die
    Quelle technisch «erfolgreich» (mit 0 Treffern) lief — diese Prüfung schliesst
    genau diese Lücke."""
    global _zero_parse_streak, _last_parse_alert
    stats = email_source.LAST_RUN
    if stats.get("fetched", 0) <= 0:
        return  # keine neuen Mails -> kein Signal (Streak nicht verändern)
    if stats.get("parsed", 0) > 0:
        _zero_parse_streak = 0   # es kommt etwas durch -> alles gut
        return
    _zero_parse_streak += 1
    if _zero_parse_streak < _PARSE_STREAK_THRESHOLD:
        return
    now = datetime.now(timezone.utc)
    if (_last_parse_alert
            and (now - _last_parse_alert).total_seconds() < _PARSE_ALERT_INTERVAL_H * 3600):
        return
    notify.send_system(
        config.TELEGRAM_BOT_TOKEN,
        config.TELEGRAM_CHAT_IDS,
        f"⚠️ <b>Möglicher Parser-/Template-Bruch</b>: {stats['fetched']} Portal-Mail(s) "
        f"empfangen, aber 0 Inserate geparst (in Folge {_zero_parse_streak}×).\n\n"
        "Ein Portal hat evtl. sein Mail-Layout geändert. Auf dem VPS prüfen:\n"
        "<code>python main.py --dump-emails</code>",
    )
    _last_parse_alert = now
    print(f"Parser-Bruch-Warnung gesendet (streak={_zero_parse_streak}).")


def _build_search(filter_state: dict) -> dict:
    """Baut den Such-Dict aus dem DB-Filter-State + statischer BBox."""
    return {
        "min_price": 0,
        "max_price": filter_state.get("max_price") or config.SEARCH.get("max_price", 9999),
        "min_rooms": filter_state.get("min_rooms") or config.SEARCH.get("min_rooms", 1),
        "max_rooms": filter_state.get("max_rooms") or config.SEARCH.get("max_rooms", 9),
        "min_space": filter_state.get("min_space") or config.SEARCH.get("min_space", 0),
        "bbox":      config.SEARCH["bbox"],   # BBox bleibt statisch
    }


def _check_heartbeat():
    """Warnt in der Gruppe wenn 24h kein einziges Inserat durch die Pipeline kam."""
    global _last_heartbeat_sent
    last_str = db.get_last_activity(config.DB_PATH)
    if not last_str:
        return  # noch nie aktiv, kein Alarm
    try:
        last_dt = datetime.fromisoformat(last_str).replace(tzinfo=timezone.utc)
        now     = datetime.now(timezone.utc)
        hours   = (now - last_dt).total_seconds() / 3600
        if hours < _HEARTBEAT_HOURS:
            return
        # Nur einmal pro 24h warnen, nicht bei jedem 15-Min-Durchlauf
        if _last_heartbeat_sent and (now - _last_heartbeat_sent).total_seconds() < 86400:
            return
        notify.send_system(
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_IDS,
            f"⚠️ Heartbeat: seit {hours:.0f}h kein einziges Inserat in der Pipeline.\n"
            f"IMAP-Verbindung oder Portal-Abos prüfen.",
        )
        _last_heartbeat_sent = now
        print(f"Heartbeat-Warnung gesendet ({hours:.0f}h ohne Aktivität).")
    except Exception as e:
        print(f"Heartbeat-Check fehlgeschlagen: {e}")


def _resend_pending() -> int:
    """Versucht Inserate erneut zu senden, die in der DB liegen, aber nie
    erfolgreich zugestellt wurden (Versand-Blip/Rate-Limit/Crash zwischen upsert
    und mark_seen). Stoppt beim ersten erneuten Fehlschlag — wenn Telegram noch
    nicht erreichbar ist, hat ein Weiterversuch jetzt keinen Sinn."""
    pending = db.get_unsent_listings(config.DB_PATH, max_age_days=2, limit=10)
    sent = 0
    for l in pending:
        if notify.send(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, l):
            db.mark_seen(config.DB_PATH, l.id, l.source, l.url)
            sent += 1
        else:
            break
    if sent:
        print(f"Resend-Sweep: {sent} offene(s) Inserat(e) nachgesendet.")
    return sent


def run_once(seed=False):
    db.init(config.DB_PATH)
    db.init_filter_state(config.DB_PATH, config.SEARCH)

    filter_state = db.get_filter_state(config.DB_PATH)
    paused       = filter_state.get("paused", False)
    search       = _build_search(filter_state)

    new_count        = 0
    successful_srcs  = 0  # Quellen die ohne Exception durchliefen

    # Zuerst Liegengebliebenes nachsenden (z.B. nach Telegram-/Netzausfall),
    # damit der Rückstand abgebaut wird, bevor neue Mails verarbeitet werden.
    if not seed and not paused:
        new_count += _resend_pending()

    for src in SOURCES:
        name = src.__name__
        try:
            listings = src(search, config)
            _source_failures[name] = 0  # Fehler-Zähler zurücksetzen
            successful_srcs += 1        # Quelle hat funktioniert (auch 0 Treffer = OK)
            _auth_ok()                  # Login klappt wieder -> Warn-Zustand löschen
            _note_parser_health()       # stillen Parser-/Template-Bruch erkennen
        except Exception as e:
            print(f"Quelle {name} fehlgeschlagen: {e}")
            if email_source.is_transient_error(e):
                # Vorübergehend (Drossel/Netz). _connect hat bereits mit Backoff
                # wiederholt; kein Alarm, kein Hard-Fail-Zähler — nächster
                # Durchlauf greift erneut. Der 24h-Heartbeat fängt einen echten
                # Dauerausfall trotzdem ab.
                continue
            if _is_auth_error(e):
                _auth_alert(e)          # tote Zugangsdaten sofort + wiederholt melden
            _source_failures[name] = _source_failures.get(name, 0) + 1
            if _source_failures[name] == _MAX_FAILURES:
                notify.send_system(
                    config.TELEGRAM_BOT_TOKEN,
                    config.TELEGRAM_CHAT_IDS,
                    f"⚠️ Quelle {name} ist {_MAX_FAILURES}× hintereinander fehlgeschlagen:\n{e}",
                )
            continue

        # Nachfiltern (PLZ, Exclude-Keywords; Preis/Zimmer bei Mail-Quelle)
        listings = db.apply_filter(listings, filter_state)

        for l in listings:
            # Immer in listings-Tabelle speichern (für /info, /merk, /weg)
            db.upsert_listing(config.DB_PATH, l)

            if not db.is_new(config.DB_PATH, l.id):
                continue

            # Cross-Portal-Dedup: gleiches Inserat von anderem Portal bereits bekannt?
            dupe_id = db.find_cross_portal_duplicate(config.DB_PATH, l)
            if dupe_id:
                print(f"Cross-Portal-Duplikat: {l.id} ≈ {dupe_id} — übersprungen")
                db.mark_seen(config.DB_PATH, l.id, l.source, l.url)
                continue

            if seed or paused:
                # konsumieren ohne Versand (wie bisher: gesehen, aber nichts senden)
                db.mark_seen(config.DB_PATH, l.id, l.source, l.url)
                continue

            if notify.send(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, l):
                new_count += 1
                db.mark_seen(config.DB_PATH, l.id, l.source, l.url)
            else:
                # Versand fehlgeschlagen -> NICHT als gesehen markieren. Das Inserat
                # liegt via upsert in der listings-Tabelle; der Resend-Sweep im
                # nächsten Durchlauf versucht es erneut -> kein stiller Verlust.
                print(f"Telegram-Versand fehlgeschlagen für {l.id} — bleibt offen (Resend folgt)")

    # Heartbeat: Aktivität tracken wenn mindestens eine Quelle erfolgreich lief
    # (auch 0 Treffer = Pipeline funktioniert — kein Alarm nötig)
    if successful_srcs > 0:
        db.set_last_activity(config.DB_PATH)

    if not seed:
        _check_heartbeat()

    if seed:
        print(f"Seed fertig. {db.count(config.DB_PATH)} Inserate als gesehen markiert.")
    elif paused:
        print("Durchlauf fertig. Bot ist pausiert — keine Nachrichten gesendet.")
    else:
        print(f"Durchlauf fertig. {new_count} neue Inserate gesendet.")


if __name__ == "__main__":
    if "--dump-emails-all" in sys.argv:
        dump_emails(config, all_emails=True)
    elif "--dump-emails" in sys.argv:
        dump_emails(config)
    elif "--backup" in sys.argv:
        db.init(config.DB_PATH)
        r = db.backup_db(config.DB_PATH)
        if r["ok"]:
            print(f"Backup ok: {r['file']} ({r['size']} B), integrity={r['integrity']}, {r['kept']} behalten.")
        else:
            print(f"Backup-Problem: {r.get('error') or r['integrity']}")
            sys.exit(1)
    elif "--seed" in sys.argv:
        run_once(seed=True)
    elif "--loop" in sys.argv:
        while True:
            run_once()
            time.sleep(config.POLL_INTERVAL_MIN * 60)
    else:
        run_once()
