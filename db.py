"""
Datenbank-Schicht (SQLite).

Tabellen:
  seen          Dedup-Tabelle (bleibt rückwärtskompatibel)
  listings      Erweiterte Inserat-Daten (für /info, /merk, /weg)
  filter_state  Einzeilig: aktueller Filter-State (überlebt Neustarts)

Migration: init_filter_state() seedet filter_state beim ersten Start
aus config.SEARCH. Danach ist die DB führend.
"""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


@contextmanager
def _conn(path):
    # timeout/busy_timeout: drei Prozesse (main-loop, command, backfill) teilen
    # sich die DB. WAL erlaubt parallele Leser + 1 Schreiber; busy_timeout lässt
    # einen kollidierenden Schreiber kurz warten statt sofort «database is locked».
    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init(path):
    with _conn(path) as con:
        # WAL-Modus: erlaubt gleichzeitige Leser + 1 Schreiber (main + command)
        con.execute("PRAGMA journal_mode=WAL")

        # Bestehende Dedup-Tabelle (rückwärtskompatibel)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                id         TEXT PRIMARY KEY,
                source     TEXT,
                url        TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Erweiterte Inserat-Tabelle (für Befehle /info, /merk, /weg)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id         TEXT PRIMARY KEY,
                source     TEXT,
                url        TEXT,
                title      TEXT,
                price      TEXT,
                rooms      TEXT,
                space      TEXT,
                location   TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen  TEXT DEFAULT CURRENT_TIMESTAMP,
                marked     TEXT
            )
            """
        )

        # Filter-State: genau eine Zeile (id=1), überlebt Neustarts
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS filter_state (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                max_price     REAL,
                min_rooms     REAL,
                max_rooms     REAL,
                min_space     REAL,
                plz_list      TEXT DEFAULT '[]',
                exclude_kw    TEXT DEFAULT '[]',
                paused        INTEGER DEFAULT 0,
                last_activity TEXT
            )
            """
        )
        # Parse-Versuche pro Mail (Message-ID): erlaubt, eine Mail bei 0 geparsten
        # Inseraten ein paar Durchläufe lang ungelesen zu lassen (Recovery bei
        # transientem Parser-/Netz-Problem), ohne Bestätigungs-/Nicht-Inserat-
        # Mails ewig erneut zu verarbeiten. Siehe email_source.fetch_email.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS email_attempts (
                msgid      TEXT PRIMARY KEY,
                attempts   INTEGER DEFAULT 0,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Migrationen für bestehende DBs
        for col_sql in [
            "ALTER TABLE filter_state ADD COLUMN last_activity TEXT",
            "ALTER TABLE filter_state ADD COLUMN kw_list TEXT DEFAULT '[]'",
            "ALTER TABLE filter_state ADD COLUMN verfuegbar_ab TEXT",
            "ALTER TABLE listings ADD COLUMN available TEXT",
            "ALTER TABLE listings ADD COLUMN marked_at TEXT",
            "ALTER TABLE listings ADD COLUMN price_num REAL",
            "ALTER TABLE listings ADD COLUMN note TEXT",
        ]:
            try:
                con.execute(col_sql)
            except Exception:
                pass  # Spalte existiert bereits


# --- Parse-Versuche pro Mail -----------------------------------------------

def bump_email_attempt(path, msgid: str) -> int:
    """Zählt einen Parse-Versuch für diese Mail (Message-ID) hoch und gibt die
    neue Gesamtzahl zurück. Ohne Message-ID (manche Mails haben keine) wird 1
    zurückgegeben, damit der Aufrufer sie einmalig behandelt und dann aufgibt."""
    if not msgid:
        return 1
    with _conn(path) as con:
        con.execute(
            "INSERT INTO email_attempts (msgid, attempts) VALUES (?, 1) "
            "ON CONFLICT(msgid) DO UPDATE SET attempts = attempts + 1",
            (msgid,),
        )
        return con.execute(
            "SELECT attempts FROM email_attempts WHERE msgid = ?", (msgid,)
        ).fetchone()[0]


# --- Filter-State -----------------------------------------------------------

def init_filter_state(path, config_search: dict):
    """Seedet filter_state beim ersten Start aus config.SEARCH.
    Danach ist die DB führend — wird nicht überschrieben."""
    with _conn(path) as con:
        count = con.execute("SELECT COUNT(*) FROM filter_state").fetchone()[0]
        if count == 0:
            con.execute(
                """
                INSERT INTO filter_state
                    (id, max_price, min_rooms, max_rooms, min_space, plz_list, exclude_kw, kw_list, paused)
                VALUES (1, ?, ?, ?, ?, '[]', '[]', '[]', 0)
                """,
                (
                    config_search.get("max_price"),
                    config_search.get("min_rooms"),
                    config_search.get("max_rooms"),
                    config_search.get("min_space", 0),
                ),
            )
            print("Filter-State aus config.SEARCH initialisiert.")


def get_filter_state(path) -> dict:
    """Gibt den aktuellen Filter-State als Dict zurück."""
    with _conn(path) as con:
        row = con.execute("SELECT * FROM filter_state WHERE id = 1").fetchone()
        if not row:
            return {}
        d = dict(row)
        d["plz_list"]   = json.loads(d.get("plz_list")   or "[]")
        d["exclude_kw"] = json.loads(d.get("exclude_kw") or "[]")
        d["kw_list"]    = json.loads(d.get("kw_list")    or "[]")
        d["paused"]     = bool(d.get("paused", 0))
        return d


def set_filter_state(path, **kwargs):
    """Updatet einzelne Felder im Filter-State (partial update)."""
    if not kwargs:
        return
    updates = {}
    for k, v in kwargs.items():
        if isinstance(v, list):
            updates[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            updates[k] = int(v)
        else:
            updates[k] = v
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values())
    with _conn(path) as con:
        con.execute(f"UPDATE filter_state SET {set_clause} WHERE id = 1", values)


# --- Listings-Tabelle -------------------------------------------------------

def upsert_listing(path, listing):
    """Speichert oder aktualisiert ein Inserat (last_seen + Felder)."""
    available  = getattr(listing, "available", None)
    price_num  = _parse_price(listing.price)
    with _conn(path) as con:
        con.execute(
            """
            INSERT INTO listings (id, source, url, title, price, price_num, rooms, space, location, available)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_seen = CURRENT_TIMESTAMP,
                title     = excluded.title,
                price     = excluded.price,
                price_num = excluded.price_num,
                rooms     = excluded.rooms,
                space     = excluded.space,
                location  = excluded.location,
                url       = excluded.url,
                available = COALESCE(excluded.available, listings.available)
            """,
            (
                listing.id, listing.source, listing.url,
                listing.title, listing.price, price_num, listing.rooms,
                listing.space, listing.location, available,
            ),
        )


def get_listing(path, query: str) -> dict | None:
    """Sucht ein Inserat per exakter ID oder PLZ (4 Ziffern -> jüngstes)."""
    query = query.strip()
    with _conn(path) as con:
        # Exakte ID (z.B. "flatfox-998877")
        row = con.execute(
            "SELECT * FROM listings WHERE id = ?", (query,)
        ).fetchone()
        if row:
            return dict(row)
        # PLZ-Suche: 4-stellige Zahl -> jüngstes Inserat mit dieser PLZ
        if re.match(r"^\d{4}$", query):
            row = con.execute(
                "SELECT * FROM listings WHERE location LIKE ?"
                " ORDER BY last_seen DESC, rowid DESC LIMIT 1",
                (f"{query}%",),
            ).fetchone()
            if row:
                return dict(row)
    return None


def mark_listing(path, query: str, marked: str | None) -> bool:
    """Setzt marked-Feld auf 'interesting', 'done' oder None.
    Gibt True zurück wenn ein Inserat gefunden wurde."""
    listing = get_listing(path, query)
    if not listing:
        return False
    with _conn(path) as con:
        if marked == "interesting":
            con.execute(
                "UPDATE listings SET marked = ?, marked_at = CURRENT_TIMESTAMP WHERE id = ?",
                (marked, listing["id"]),
            )
        else:
            con.execute(
                "UPDATE listings SET marked = ? WHERE id = ?",
                (marked, listing["id"]),
            )
    return True


def count_stats(path) -> dict:
    """Gibt Statistiken über gesehene/gemerkete Inserate zurück."""
    with _conn(path) as con:
        total = con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        def _count(marked):
            return con.execute(
                "SELECT COUNT(*) FROM listings WHERE marked = ?", (marked,)
            ).fetchone()[0]
        interesting  = _count("interesting")
        beworben     = _count("beworben")
        besichtigung = _count("besichtigung")
        abgelehnt    = _count("abgelehnt")
        done         = _count("done")
        new_today = con.execute(
            "SELECT COUNT(*) FROM listings WHERE date(first_seen) = date('now')"
        ).fetchone()[0]
        row = con.execute(
            "SELECT last_activity FROM filter_state WHERE id = 1"
        ).fetchone()
    return {
        "total_seen":   total,
        "interesting":  interesting,
        "beworben":     beworben,
        "besichtigung": besichtigung,
        "abgelehnt":    abgelehnt,
        "done":         done,
        "new_today":    new_today,
        "last_activity": row[0] if row else None,
    }


def get_interesting_today(path) -> list[dict]:
    """Gibt heute als 'interesting' markierte Inserate zurück."""
    with _conn(path) as con:
        rows = con.execute(
            "SELECT * FROM listings WHERE marked = 'interesting'"
            " AND date(marked_at) = date('now')"
            " ORDER BY marked_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


# --- Aktivitäts-Tracking (für Heartbeat) ------------------------------------

def set_last_activity(path):
    """Aktualisiert den Zeitstempel der letzten Pipeline-Aktivität."""
    with _conn(path) as con:
        con.execute(
            "UPDATE filter_state SET last_activity = CURRENT_TIMESTAMP WHERE id = 1"
        )


def get_last_activity(path) -> str | None:
    """Gibt den letzten Aktivitäts-Zeitstempel zurück (ISO-String oder None)."""
    with _conn(path) as con:
        row = con.execute(
            "SELECT last_activity FROM filter_state WHERE id = 1"
        ).fetchone()
        return row[0] if row else None


# --- Merkliste ---------------------------------------------------------------

def get_interesting_listings(path) -> list[dict]:
    """Gibt alle als 'interesting' markierten Inserate zurück."""
    with _conn(path) as con:
        rows = con.execute(
            "SELECT * FROM listings WHERE marked = 'interesting' ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_open_listings(path) -> list[dict]:
    """Gibt alle Inserate mit offenem Bewerbungs-Status zurück (beworben/besichtigung)."""
    with _conn(path) as con:
        rows = con.execute(
            "SELECT * FROM listings WHERE marked IN ('beworben','besichtigung')"
            " ORDER BY marked DESC, last_seen DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def cleanup_done(path) -> dict:
    """Löscht alle als 'done' markierten Inserate aus listings und seen.
    Gibt {'listings': n, 'seen': n} zurück."""
    with _conn(path) as con:
        done_ids = [r[0] for r in con.execute(
            "SELECT id FROM listings WHERE marked = 'done'"
        ).fetchall()]
        if not done_ids:
            return {"listings": 0, "seen": 0}
        placeholders = ",".join("?" * len(done_ids))
        con.execute(f"DELETE FROM listings WHERE id IN ({placeholders})", done_ids)
        con.execute(f"DELETE FROM seen    WHERE id IN ({placeholders})", done_ids)
    return {"listings": len(done_ids), "seen": len(done_ids)}


# --- Bestehende Dedup-Funktionen (rückwärtskompatibel) ----------------------

def is_new(path, listing_id):
    with _conn(path) as con:
        cur = con.execute("SELECT 1 FROM seen WHERE id = ?", (listing_id,))
        return cur.fetchone() is None


def mark_seen(path, listing_id, source, url):
    with _conn(path) as con:
        con.execute(
            "INSERT OR IGNORE INTO seen (id, source, url) VALUES (?, ?, ?)",
            (listing_id, source, url),
        )


def count(path):
    with _conn(path) as con:
        return con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def backup_db(path, dest_dir: str | None = None, keep: int = 14) -> dict:
    """Konsistentes Backup der SQLite-DB — auch im laufenden Betrieb sicher.

    Nutzt `VACUUM INTO` (sauberer, kompakter Snapshot inkl. WAL-Inhalt) statt
    rohem Dateikopieren, das mit WAL inkonsistent sein kann. Davor ein schneller
    Integritätscheck (PRAGMA quick_check). Behält die letzten `keep` Snapshots.

    Rückgabe: dict(ok, integrity, file, size, kept, error)."""
    res = {"ok": False, "integrity": None, "file": None, "size": 0, "kept": 0, "error": None}
    try:
        if dest_dir is None:
            base = os.path.dirname(os.path.abspath(path)) or "."
            dest_dir = os.path.join(base, "backups")
        os.makedirs(dest_dir, exist_ok=True)

        # Mikrosekunden im Namen: VACUUM INTO scheitert bei existierender Datei,
        # zwei Backups in derselben Sekunde würden sonst kollidieren.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        out = os.path.join(dest_dir, f"listings-{stamp}.db")

        # Eigene Verbindung im Autocommit-Modus: VACUUM darf NICHT in einer
        # offenen Transaktion laufen (sonst «cannot VACUUM from within a transaction»).
        con = sqlite3.connect(path, timeout=30.0)
        con.isolation_level = None
        try:
            res["integrity"] = con.execute("PRAGMA quick_check").fetchone()[0]
            safe = out.replace("'", "''")   # Pfad für SQL-String-Literal quoten
            con.execute(f"VACUUM INTO '{safe}'")
        finally:
            con.close()

        res["file"] = out
        res["size"] = os.path.getsize(out)

        # Rotation: nur unsere Snapshots, älteste über `keep` hinaus löschen
        snaps = sorted(
            f for f in os.listdir(dest_dir)
            if f.startswith("listings-") and f.endswith(".db")
        )
        if keep > 0:
            for old in snaps[:-keep]:
                try:
                    os.remove(os.path.join(dest_dir, old))
                except OSError:
                    pass
        res["kept"] = min(len(snaps), keep) if keep > 0 else len(snaps)
        res["ok"] = (res["integrity"] == "ok")
    except Exception as e:
        res["error"] = str(e)
    return res


def get_unsent_listings(path, max_age_days: int = 2, limit: int = 10) -> list:
    """Inserate, die in `listings` stehen, aber nie in `seen` kamen — also nie
    erfolgreich zugestellt wurden (Versand-Blip/Rate-Limit/Crash zwischen upsert
    und mark_seen). Begrenzt auf junge, nicht erledigte Inserate, damit kein
    Alt-Bestand nachträglich rausgeschickt wird. Rückgabe als Listing-Objekte
    für notify.send() (ohne Bild -> Versand als Text)."""
    from sources import Listing
    with _conn(path) as con:
        rows = con.execute(
            """
            SELECT * FROM listings l
            WHERE NOT EXISTS (SELECT 1 FROM seen s WHERE s.id = l.id)
              AND (l.marked IS NULL OR l.marked NOT IN ('done', 'abgelehnt'))
              AND l.first_seen >= datetime('now', ?)
            ORDER BY l.first_seen ASC
            LIMIT ?
            """,
            (f"-{int(max_age_days)} days", int(limit)),
        ).fetchall()
    return [
        Listing(
            id=r["id"], source=r["source"], title=r["title"] or "Wohnung",
            price=r["price"] or "?", rooms=r["rooms"] or "?",
            space=r["space"] or "?", location=r["location"] or "—",
            url=r["url"] or "", image=None, available=r["available"],
        )
        for r in rows
    ]


# --- Filter-Logik -----------------------------------------------------------

_MONTH_MAP = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "dezember": 12, "january": 1, "february": 2, "march": 3, "may": 5,
    "june": 6, "july": 7, "october": 10, "december": 12,
}


def _parse_available_ym(text: str) -> tuple[int, int] | None:
    """Extrahiert (Jahr, Monat) aus einem available-String. None wenn nicht parsebar."""
    if not text:
        return None
    low = text.lower()
    if "sofort" in low:
        return None  # ab sofort = immer ok
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", text)
    if m:
        return int(m.group(3)), int(m.group(2))
    for name, num in _MONTH_MAP.items():
        if name in low:
            m = re.search(r"\b(20\d{2})\b", text)
            if m:
                return int(m.group(1)), num
    m = re.search(r"\b(20\d{2})-(\d{2})\b", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _parse_price(s) -> float | None:
    """'CHF 1'800' oder '1800' -> 1800.0, '?' -> None."""
    if not s or str(s).strip() in ("?", "—", ""):
        return None
    cleaned = re.sub(r"[^\d]", "", str(s))
    return float(cleaned) if cleaned else None


def _parse_rooms(s) -> float | None:
    """'3.5' oder '3,5' -> 3.5, '?' -> None."""
    if not s or str(s).strip() in ("?", "—", ""):
        return None
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return None


def apply_filter(listings: list, filter_state: dict) -> list:
    """Wendet den Filter-State auf eine Liste von Listings an.
    Fehlende/unparsbare Felder -> Inserat durchlassen (lieber zu viel)."""
    if not filter_state:
        return listings

    max_price    = filter_state.get("max_price")
    min_rooms    = filter_state.get("min_rooms")
    max_rooms    = filter_state.get("max_rooms")
    min_space    = filter_state.get("min_space")
    plz_list     = filter_state.get("plz_list", [])
    exclude_kw   = filter_state.get("exclude_kw", [])
    kw_list      = filter_state.get("kw_list", [])
    verfuegbar_ab = filter_state.get("verfuegbar_ab")  # "YYYY-MM" oder None

    out = []
    for l in listings:
        # --- Preis-Filter ---
        if max_price:
            price_num = _parse_price(l.price)
            if price_num is not None and price_num > max_price:
                continue

        # --- Zimmer-Filter ---
        rooms_num = _parse_rooms(l.rooms)
        if rooms_num is not None:
            if min_rooms and rooms_num < min_rooms:
                continue
            if max_rooms and rooms_num > max_rooms:
                continue

        # --- Flächen-Filter ---
        if min_space:
            space_num = _parse_rooms(l.space)  # gleiche Parsing-Logik
            if space_num is not None and space_num < min_space:
                continue

        # --- PLZ-Filter (HARTES Kriterium) ---
        # PLZ ist das wichtigste Kriterium und wird strikt durchgesetzt: bei
        # gesetzter Liste MUSS das Inserat eine erkannte PLZ aus der Liste haben.
        # Inserate ohne erkennbare PLZ werden VERWORFEN (Ausnahme vom sonstigen
        # "lieber durchlassen"-Prinzip — bewusst, damit kein Inserat ausserhalb
        # des Gebiets durchrutscht).
        if plz_list:
            loc = (l.location or "").strip()
            plz_m = re.match(r"^(\d{4})\b", loc)
            if not plz_m or plz_m.group(1) not in plz_list:
                continue

        # --- Exclude-Keywords ---
        if exclude_kw:
            haystack = f"{l.title} {l.location}".lower()
            if any(kw.strip().lower() in haystack for kw in exclude_kw if kw.strip()):
                continue

        # --- Keyword-Whitelist ---
        # Mindestens ein Keyword muss vorkommen; leer = kein Filter.
        if kw_list:
            haystack = f"{l.title} {l.location}".lower()
            if not any(kw.strip().lower() in haystack for kw in kw_list if kw.strip()):
                continue

        # --- Verfügbarkeits-Filter ---
        # Inserate die erst nach dem Zieldatum frei sind, werden übersprungen.
        # Kein available-Feld oder "ab sofort" -> immer durchlassen.
        if verfuegbar_ab:
            try:
                target_y, target_m = map(int, verfuegbar_ab.split("-"))
                avail_ym = _parse_available_ym(getattr(l, "available", None) or "")
                if avail_ym is not None and avail_ym > (target_y, target_m):
                    continue
            except Exception:
                pass

        out.append(l)
    return out


def find_cross_portal_duplicate(path, listing) -> str | None:
    """Sucht ein Inserat mit gleicher PLZ + Preis (±2%) von anderem Portal.
    Gibt die ID des Duplikats zurück oder None."""
    plz_m = re.match(r"^(\d{4})", (listing.location or "").strip())
    price_num = _parse_price(listing.price)
    if not plz_m or price_num is None:
        return None
    plz       = plz_m.group(1)
    tolerance = max(price_num * 0.02, 20)
    with _conn(path) as con:
        row = con.execute(
            """SELECT id FROM listings
               WHERE location LIKE ?
               AND price_num BETWEEN ? AND ?
               AND source != ?
               AND id != ?
               ORDER BY last_seen DESC LIMIT 1""",
            (f"{plz}%", price_num - tolerance, price_num + tolerance,
             listing.source, listing.id),
        ).fetchone()
    return row[0] if row else None


def set_note(path, listing_id: str, note: str) -> bool:
    """Speichert eine Freitext-Notiz zu einem Inserat. Gibt True zurück wenn gefunden."""
    with _conn(path) as con:
        cur = con.execute(
            "UPDATE listings SET note = ? WHERE id = ?", (note.strip() or None, listing_id)
        )
    return cur.rowcount > 0
