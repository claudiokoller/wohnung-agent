"""
Einmaliger Backfill: parst die gedumpten Portal-Mails (email_dumps/*.html),
dedupe + Filter, und sendet brauchbare Inserate nachträglich an Telegram.

Hintergrund: Während des IMAP-Auth-Ausfalls (2026-06-02 bis -06-07) kam nichts
durch. Die Mails sind inzwischen alle als gelesen markiert, der Live-Bot fasst
sie nie wieder an (sucht nur UNSEEN). Dieses Skript holt die Inserate aus den
HTML-Dumps nach.

  python backfill.py              Trockenlauf: nur Zahlen + Beispiele
  python backfill.py --send       Sendet wirklich + markiert als gesehen
  python backfill.py --send --min N   Mindest-Datenscore (default 3)
"""
import dataclasses
import glob
import json
import os
import sys
import time

import config
import db
import notify
import email_source as es
from sources import Listing

CACHE = "backfill_cache.json"


def parse_all(dump_dir="email_dumps"):
    """Parst alle gedumpten Mails, dedupe nach Listing-ID (reichste Daten gewinnen).
    Cached das Ergebnis in CACHE, damit wiederholte Läufe nicht erneut auflösen."""
    if os.path.exists(CACHE) and "--reparse" not in sys.argv:
        with open(CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return [Listing(**d) for d in data["listings"]], data["nfiles"]

    by_id: dict = {}
    files = sorted(glob.glob(os.path.join(dump_dir, "*.html")))
    for path in files:
        with open(path, encoding="utf-8") as f:
            html = f.read()
        for l in es._parse_html(html, resolve_links=config.RESOLVE_TRACKING_LINKS):
            cur = by_id.get(l.id)
            if cur is None:
                by_id[l.id] = l
                continue
            new_s = es._data_score(l.price, l.rooms, l.space, l.location)
            old_s = es._data_score(cur.price, cur.rooms, cur.space, cur.location)
            if new_s > old_s:
                by_id[l.id] = l
    listings = list(by_id.values())
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"nfiles": len(files),
                   "listings": [dataclasses.asdict(l) for l in listings]},
                  f, ensure_ascii=False, indent=1)
    return listings, len(files)


def diagnose(listings, fs):
    """Zeigt für jedes Inserat, welches Filter-Kriterium es rejecten würde."""
    from db import _parse_price, _parse_rooms
    import re
    plz_list = fs.get("plz_list", [])
    reasons = {"plz": 0, "preis": 0, "zimmer": 0, "flaeche": 0, "ok": 0}
    print("\n--- Alle Inserate (pre-filter) ---")
    for l in sorted(listings, key=lambda x: x.location):
        why = []
        pn = _parse_price(l.price)
        if fs.get("max_price") and pn is not None and pn > fs["max_price"]:
            why.append(f"Preis {pn:.0f}>{fs['max_price']:.0f}")
        rn = _parse_rooms(l.rooms)
        if rn is not None and ((fs.get("min_rooms") and rn < fs["min_rooms"])
                               or (fs.get("max_rooms") and rn > fs["max_rooms"])):
            why.append(f"Zi {rn}")
        sn = _parse_rooms(l.space)
        if fs.get("min_space") and sn is not None and sn < fs["min_space"]:
            why.append(f"m² {sn}<{fs['min_space']:.0f}")
        pm = re.match(r"^(\d{4})\b", (l.location or "").strip())
        if plz_list and pm and pm.group(1) not in plz_list:
            why.append(f"PLZ {pm.group(1)}")
        # Zählung grober Hauptgrund
        if why:
            if any(w.startswith("PLZ") for w in why): reasons["plz"] += 1
            elif any(w.startswith("Preis") for w in why): reasons["preis"] += 1
            elif any(w.startswith("Zi") for w in why): reasons["zimmer"] += 1
            else: reasons["flaeche"] += 1
        else:
            reasons["ok"] += 1
        flag = "✅" if not why else "❌ " + ", ".join(why)
        print(f"  {l.location:20} | {l.price:12} | {l.rooms}Zi {l.space}m² | {flag}")
    print(f"\nRejection-Gründe (Hauptgrund je Inserat): {reasons}")


def main():
    send = "--send" in sys.argv
    min_score = 3
    if "--min" in sys.argv:
        min_score = int(sys.argv[sys.argv.index("--min") + 1])

    db.init(config.DB_PATH)
    db.init_filter_state(config.DB_PATH, config.SEARCH)
    fs = db.get_filter_state(config.DB_PATH)

    listings, nfiles = parse_all()
    print(f"{nfiles} Mails geparst -> {len(listings)} eindeutige Inserate (dedup nach id)")

    if "--diagnose" in sys.argv:
        diagnose(listings, fs)
        return

    filtered = db.apply_filter(listings, fs)
    print(f"Nach Filter (PLZ/Preis/Zimmer/Fläche): {len(filtered)}")

    # Score-Verteilung
    dist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for l in filtered:
        dist[es._data_score(l.price, l.rooms, l.space, l.location)] += 1
    print(f"Score-Verteilung (4=alle Felder): {dist}")

    good = [l for l in filtered
            if es._data_score(l.price, l.rooms, l.space, l.location) >= min_score]
    print(f"Sendbar (score>={min_score}): {len(good)}\n")

    for l in good:
        print(f"  [{l.source:10}] {l.location:18} | {l.price:12} | "
              f"{l.rooms}Zi {l.space}m² | {l.title[:38]}")

    if not send:
        print("\nTrockenlauf — nichts gesendet. Mit '--send' wirklich senden.")
        return

    print(f"\nSende {len(good)} Inserate (mit Pause gegen Telegram-Rate-Limit)...")
    sent = 0
    for l in good:
        db.upsert_listing(config.DB_PATH, l)
        if not db.is_new(config.DB_PATH, l.id):
            continue
        dupe = db.find_cross_portal_duplicate(config.DB_PATH, l)
        if dupe:
            db.mark_seen(config.DB_PATH, l.id, l.source, l.url)
            continue
        notify.send(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS, l)
        db.mark_seen(config.DB_PATH, l.id, l.source, l.url)
        sent += 1
        time.sleep(4)  # Telegram-Gruppen-Limit ~20 Nachrichten/Minute
    print(f"\n{sent} Inserate nachträglich gesendet.")


if __name__ == "__main__":
    main()
