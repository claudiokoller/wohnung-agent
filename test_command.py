"""
Unit-Tests für command.py (Parser) und db.apply_filter.

Läuft ohne echten Telegram-Account und ohne DB-Datei (für apply_filter
wird eine In-Memory-DB genutzt).

Ausführen:
    python -m pytest test_command.py -v
    # oder ohne pytest:
    python test_command.py
"""

import sqlite3
import tempfile
import os

from sources import Listing
import db
from command import (
    parse_preis,
    parse_zimmer,
    parse_plz,
    parse_exclude,
)


# ---------------------------------------------------------------------------
# Hilfs-Funktion: Test-Listing erstellen
# ---------------------------------------------------------------------------

def _listing(
    id="test-1",
    title="Schöne 3-Zi Wohnung",
    price="CHF 2000",
    rooms="3",
    space="70",
    location="8001 Zürich",
    source="Test",
    url="http://beispiel.ch",
):
    return Listing(
        id=id, source=source, title=title, price=price,
        rooms=rooms, space=space, location=location, url=url,
    )


# ---------------------------------------------------------------------------
# Tests: parse_preis
# ---------------------------------------------------------------------------

def test_parse_preis_normal():
    assert parse_preis("2500") == {"max_price": 2500.0}

def test_parse_preis_schweizer_trennzeichen():
    assert parse_preis("2'500") == {"max_price": 2500.0}

def test_parse_preis_mit_leerzeichen():
    assert parse_preis("  2600  ") == {"max_price": 2600.0}

def test_parse_preis_ungueltig():
    assert parse_preis("abc") is None

def test_parse_preis_null_oder_negativ():
    assert parse_preis("0") is None
    assert parse_preis("-500") is None


# ---------------------------------------------------------------------------
# Tests: parse_zimmer
# ---------------------------------------------------------------------------

def test_parse_zimmer_range():
    assert parse_zimmer("2.5-4") == {"min_rooms": 2.5, "max_rooms": 4.0}

def test_parse_zimmer_range_bindestrich():
    assert parse_zimmer("3–4.5") == {"min_rooms": 3.0, "max_rooms": 4.5}

def test_parse_zimmer_einzelwert():
    assert parse_zimmer("3") == {"min_rooms": 3.0, "max_rooms": 3.0}

def test_parse_zimmer_komma_als_dezimal():
    assert parse_zimmer("2,5-4") == {"min_rooms": 2.5, "max_rooms": 4.0}

def test_parse_zimmer_ungueltige_reihenfolge():
    # min > max -> ungültig
    assert parse_zimmer("4-2") is None

def test_parse_zimmer_ungueltig():
    assert parse_zimmer("abc") is None


# ---------------------------------------------------------------------------
# Tests: parse_plz
# ---------------------------------------------------------------------------

def test_parse_plz_leer_zeigt_liste():
    assert parse_plz("") == {"action": "show"}

def test_parse_plz_liste_setzen():
    assert parse_plz("8953,8957") == {"action": "set", "plz_list": ["8953", "8957"]}

def test_parse_plz_einzeln_setzen():
    assert parse_plz("8001") == {"action": "set", "plz_list": ["8001"]}

def test_parse_plz_add():
    assert parse_plz("add 8953") == {"action": "add", "plz": "8953"}

def test_parse_plz_del():
    assert parse_plz("del 8957") == {"action": "del", "plz": "8957"}

def test_parse_plz_add_gross():
    # Gross-/Kleinschreibung egal
    assert parse_plz("ADD 8953") == {"action": "add", "plz": "8953"}

def test_parse_plz_ungueltige_plz():
    assert parse_plz("add 12345") is None    # 5 Stellen
    assert parse_plz("del 123")   is None    # 3 Stellen
    # "add abc" ist im Parser gültig (Gemeindenamen-Validierung passiert in handle_command)

def test_parse_plz_gemischte_liste_ungueltig():
    # Wenn ein Eintrag keine gültige PLZ ist -> None
    assert parse_plz("8001,abc") is None


# ---------------------------------------------------------------------------
# Tests: parse_exclude
# ---------------------------------------------------------------------------

def test_parse_exclude_keywords():
    r = parse_exclude("studio,keller,möbliert")
    assert r == {"exclude_kw": ["studio", "keller", "möbliert"]}

def test_parse_exclude_leer():
    r = parse_exclude("")
    assert r == {"exclude_kw": []}

def test_parse_exclude_leerzeichen():
    r = parse_exclude("  studio , keller  ")
    assert r == {"exclude_kw": ["studio", "keller"]}


# ---------------------------------------------------------------------------
# Tests: db.apply_filter
# ---------------------------------------------------------------------------

def test_filter_preis_zu_hoch():
    listings = [
        _listing(id="a", price="CHF 2000"),
        _listing(id="b", price="CHF 3000"),
    ]
    state = {"max_price": 2500, "min_rooms": None, "max_rooms": None,
             "plz_list": [], "exclude_kw": []}
    result = db.apply_filter(listings, state)
    assert len(result) == 1
    assert result[0].id == "a"


def test_filter_preis_apostrophe():
    """CHF-Preise mit Schweizer Tausend-Trennzeichen (') korrekt parsen."""
    listings = [_listing(id="a", price="CHF 1'800")]
    state = {"max_price": 2000, "min_rooms": None, "max_rooms": None,
             "plz_list": [], "exclude_kw": []}
    result = db.apply_filter(listings, state)
    assert len(result) == 1


def test_filter_zimmer_range():
    listings = [
        _listing(id="zu-klein", rooms="2"),
        _listing(id="passt",    rooms="3.5"),
        _listing(id="zu-gross", rooms="5"),
    ]
    state = {"max_price": None, "min_rooms": 2.5, "max_rooms": 4.0,
             "plz_list": [], "exclude_kw": []}
    result = db.apply_filter(listings, state)
    assert [l.id for l in result] == ["passt"]


def test_filter_plz():
    listings = [
        _listing(id="a", location="8001 Zürich"),
        _listing(id="b", location="8400 Winterthur"),
    ]
    state = {"max_price": None, "min_rooms": None, "max_rooms": None,
             "plz_list": ["8001"], "exclude_kw": []}
    result = db.apply_filter(listings, state)
    assert [l.id for l in result] == ["a"]


def test_filter_plz_mehrere():
    listings = [
        _listing(id="a", location="8001 Zürich"),
        _listing(id="b", location="8004 Zürich"),
        _listing(id="c", location="8400 Winterthur"),
    ]
    state = {"max_price": None, "min_rooms": None, "max_rooms": None,
             "plz_list": ["8001", "8004"], "exclude_kw": []}
    result = db.apply_filter(listings, state)
    assert {l.id for l in result} == {"a", "b"}


def test_filter_exclude_keyword():
    listings = [
        _listing(id="a", title="Studio Wohnung"),
        _listing(id="b", title="Schöne 3-Zi Wohnung"),
    ]
    state = {"max_price": None, "min_rooms": None, "max_rooms": None,
             "plz_list": [], "exclude_kw": ["studio"]}
    result = db.apply_filter(listings, state)
    assert [l.id for l in result] == ["b"]


def test_filter_exclude_case_insensitive():
    listings = [_listing(id="a", title="STUDIO loft")]
    state = {"max_price": None, "min_rooms": None, "max_rooms": None,
             "plz_list": [], "exclude_kw": ["Studio"]}
    result = db.apply_filter(listings, state)
    assert len(result) == 0


def test_filter_fehlende_felder_durchlassen():
    """Listings mit '?' oder '—' bei aktivem Filter durchlassen."""
    listings = [
        _listing(id="a", price="?", rooms="?", location="—"),
    ]
    state = {
        "max_price": 1000,   # sehr eng
        "min_rooms": 4.0,
        "max_rooms": 4.0,
        "plz_list": ["8001"],
        "exclude_kw": [],
    }
    result = db.apply_filter(listings, state)
    assert len(result) == 1  # durchgelassen trotz enger Filter


def test_filter_kein_state():
    """Leerer State -> alle Listings durchlassen."""
    listings = [_listing(id="a"), _listing(id="b")]
    result = db.apply_filter(listings, {})
    assert len(result) == 2


def test_filter_kombination():
    """Preis + PLZ + Exclude kombiniert."""
    listings = [
        _listing(id="ok",      price="CHF 2000", rooms="3",   location="8001 Zürich",     title="Helle Wohnung"),
        _listing(id="teuer",   price="CHF 3500", rooms="3",   location="8001 Zürich",     title="Luxus"),
        _listing(id="falsche-plz", price="CHF 2000", rooms="3", location="8400 Winterthur", title="Helle Wohnung"),
        _listing(id="studio",  price="CHF 1800", rooms="1",   location="8001 Zürich",     title="Studio loft"),
    ]
    state = {
        "max_price": 2500,
        "min_rooms": 2.5,
        "max_rooms": 4.5,
        "plz_list": ["8001"],
        "exclude_kw": ["studio"],
    }
    result = db.apply_filter(listings, state)
    assert [l.id for l in result] == ["ok"]


# ---------------------------------------------------------------------------
# Tests: db.get_listing (in-memory DB)
# ---------------------------------------------------------------------------

def test_get_listing_by_id():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db.init(path)
        l = _listing(id="flatfox-99887")
        db.upsert_listing(path, l)
        result = db.get_listing(path, "flatfox-99887")
        assert result is not None
        assert result["id"] == "flatfox-99887"
    finally:
        os.unlink(path)


def test_get_listing_by_plz():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db.init(path)
        l = _listing(id="homegate-12345", location="8001 Zürich")
        db.upsert_listing(path, l)
        result = db.get_listing(path, "8001")
        assert result is not None
        assert result["id"] == "homegate-12345"
    finally:
        os.unlink(path)


def test_get_listing_plz_nimmt_juengstes():
    """Gibt es mehrere Inserate mit gleicher PLZ, wird das jüngste zurückgegeben."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db.init(path)
        db.upsert_listing(path, _listing(id="alt-1", location="8001 Zürich"))
        db.upsert_listing(path, _listing(id="neu-2", location="8001 Zürich"))
        result = db.get_listing(path, "8001")
        assert result["id"] == "neu-2"
    finally:
        os.unlink(path)


def test_get_listing_nicht_vorhanden():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db.init(path)
        assert db.get_listing(path, "flatfox-99999") is None
        assert db.get_listing(path, "9999") is None
    finally:
        os.unlink(path)


def test_mark_listing():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db.init(path)
        l = _listing(id="flatfox-42")
        db.upsert_listing(path, l)
        ok = db.mark_listing(path, "flatfox-42", "interesting")
        assert ok is True
        result = db.get_listing(path, "flatfox-42")
        assert result["marked"] == "interesting"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Einfacher Test-Runner (ohne pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]

    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK  {name}")
            passed += 1
        except Exception as e:
            print(f"  ERR {name}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} bestanden, {failed} fehlgeschlagen.")
    if failed:
        raise SystemExit(1)
