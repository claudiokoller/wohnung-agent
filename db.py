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
import re
import sqlite3
from contextlib import contextmanager


@contextmanager
def _conn(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
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
        # Migrationen für bestehende DBs
        for col_sql in [
            "ALTER TABLE filter_state ADD COLUMN last_activity TEXT",
            "ALTER TABLE listings ADD COLUMN available TEXT",
            "ALTER TABLE listings ADD COLUMN marked_at TEXT",
        ]:
            try:
                con.execute(col_sql)
            except Exception:
                pass  # Spalte existiert bereits


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
                    (id, max_price, min_rooms, max_rooms, min_space, plz_list, exclude_kw, paused)
                VALUES (1, ?, ?, ?, ?, '[]', '[]', 0)
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
    available = getattr(listing, "available", None)
    with _conn(path) as con:
        con.execute(
            """
            INSERT INTO listings (id, source, url, title, price, rooms, space, location, available)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_seen = CURRENT_TIMESTAMP,
                title     = excluded.title,
                price     = excluded.price,
                rooms     = excluded.rooms,
                space     = excluded.space,
                location  = excluded.location,
                url       = excluded.url,
                available = COALESCE(excluded.available, listings.available)
            """,
            (
                listing.id, listing.source, listing.url,
                listing.title, listing.price, listing.rooms,
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
        interesting = con.execute(
            "SELECT COUNT(*) FROM listings WHERE marked = 'interesting'"
        ).fetchone()[0]
        done = con.execute(
            "SELECT COUNT(*) FROM listings WHERE marked = 'done'"
        ).fetchone()[0]
        row = con.execute(
            "SELECT last_activity FROM filter_state WHERE id = 1"
        ).fetchone()
    return {
        "total_seen": total,
        "interesting": interesting,
        "done": done,
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


# --- Filter-Logik -----------------------------------------------------------

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

    max_price  = filter_state.get("max_price")
    min_rooms  = filter_state.get("min_rooms")
    max_rooms  = filter_state.get("max_rooms")
    min_space  = filter_state.get("min_space")
    plz_list   = filter_state.get("plz_list", [])
    exclude_kw = filter_state.get("exclude_kw", [])

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

        # --- PLZ-Filter ---
        # Nur filtern wenn in der Location eine 4-stellige PLZ erkannt wird.
        # Fehlende/unparsbare Location -> durchlassen.
        if plz_list:
            loc = (l.location or "").strip()
            plz_m = re.match(r"^(\d{4})\b", loc)
            if plz_m and plz_m.group(1) not in plz_list:
                continue

        # --- Exclude-Keywords ---
        if exclude_kw:
            haystack = f"{l.title} {l.location}".lower()
            if any(kw.strip().lower() in haystack for kw in exclude_kw if kw.strip()):
                continue

        out.append(l)
    return out
