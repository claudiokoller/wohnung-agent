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
import notify
from application import build_blank_letter, build_letter, imap_append_draft, save_file
from email_source import dump_emails, fetch_email

# Jede Quelle: (search, cfg) -> list[Listing]
SOURCES = [fetch_email]

# Fehler-Tracking: wie oft hat eine Quelle hintereinander versagt?
_source_failures: dict[str, int] = {}
_MAX_FAILURES = 3          # ab hier Telegram-Warnung
_HEARTBEAT_HOURS = 24      # nach X Stunden ohne Aktivität warnen
_last_heartbeat_sent: datetime | None = None


def _dispatch_draft(listing):
    try:
        subject, body       = build_letter(listing, config)
        subject_b, body_b   = build_blank_letter(listing, config)
    except Exception as e:
        print(f"Entwurf konnte nicht erstellt werden ({listing.id}): {e}")
        return
    if config.DRAFT_IN_TELEGRAM:
        # Option 1: fertige Vorlage
        notify.send_draft(
            config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject, body,
            phone=config.APPLICANT.get("phone", ""),
            email=config.APPLICANT.get("email", ""),
        )
        # Option 2: leere Struktur zum manuellen Ausfüllen
        notify.send_blank(
            config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, subject_b, body_b,
            listing_url=listing.url,
        )
    if config.DRAFT_SAVE_FILES:
        save_file(listing, subject, body)
    if config.DRAFT_IMAP_APPEND:
        imap_append_draft(listing, subject, body, config)


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


def run_once(seed=False):
    db.init(config.DB_PATH)
    db.init_filter_state(config.DB_PATH, config.SEARCH)

    filter_state = db.get_filter_state(config.DB_PATH)
    paused       = filter_state.get("paused", False)
    search       = _build_search(filter_state)

    new_count        = 0
    successful_srcs  = 0  # Quellen die ohne Exception durchliefen

    for src in SOURCES:
        name = src.__name__
        try:
            listings = src(search, config)
            _source_failures[name] = 0  # Fehler-Zähler zurücksetzen
            successful_srcs += 1        # Quelle hat funktioniert (auch 0 Treffer = OK)
        except Exception as e:
            print(f"Quelle {name} fehlgeschlagen: {e}")
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

            if not seed and not paused:
                notify.send(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, l)
                _dispatch_draft(l)
                new_count += 1

            db.mark_seen(config.DB_PATH, l.id, l.source, l.url)

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
    if "--dump-emails" in sys.argv:
        dump_emails(config)
    elif "--seed" in sys.argv:
        run_once(seed=True)
    elif "--loop" in sys.argv:
        while True:
            run_once()
            time.sleep(config.POLL_INTERVAL_MIN * 60)
    else:
        run_once()
