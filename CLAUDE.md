# Wohnungs-Bot Zürich-Region — Projekt-Kontext für Claude Code

Diese Datei wird von Claude Code automatisch als Kontext geladen.

## Was der Bot tut

Aggregiert Schweizer Mietinserate aus mehreren Portalen in einen
Telegram-Feed für eine 2er-WG-Suche (Max + Sam). Architektur:

- **Homegate / ImmoScout24 / newhome / Flatfox** → Suchabos auf
  wohnung.suchen@example.com; Bot pollt per IMAP, parst die Mails
- Pipeline: `Listing`-Objekte → SQLite-Dedup (inkl. Cross-Portal) →
  Filterung → Telegram-Versand an Gruppe `-1001234567890`

## Modulübersicht

| Datei | Aufgabe |
|---|---|
| `config.py` | Token, IMAP-Zugang, Suchkriterien (statisch), `APPLICANT`-Profil |
| `sources.py` | `Listing`-Datentyp + `telegram_text()` (Maps-Link, Preis/m²) |
| `email_source.py` | IMAP-Parser: parallele Link-Auflösung, DOM-Traversal, `dump_emails()` |
| `db.py` | SQLite: seen/listings/filter_state, apply_filter, Cross-Portal-Dedup |
| `notify.py` | Telegram: `send()` (4 Buttons), `send_draft()`, `send_daily_summary()` |
| `application.py` | Bewerbungsvorlage (deterministisch, kein LLM) |
| `command.py` | Telegram Long-Polling, alle Befehle, tägliche Zusammenfassung 20h |
| `main.py` | Orchestrierung: `--seed`, `--loop`, `--dump-emails` |
| `plz_lookup.py` | PLZ ↔ Gemeindename für Kanton Zürich (162 Gemeinden) |
| `test_command.py` | Unit-Tests (37 Tests, `python test_command.py`) |

## Alle Telegram-Befehle

### Filter
| Befehl | Wirkung |
|---|---|
| `/preis 2500` | Maximalpreis CHF |
| `/zimmer 2.5-4` | Zimmer-Range |
| `/flaeche 60` | Mindestfläche m² |
| `/plz 8001,8004` | PLZ-Filter setzen |
| `/plz add Schlieren` | PLZ per Gemeindename hinzufügen |
| `/plz del 8957` | PLZ entfernen |
| `/plz` | Aktuelle PLZ-Liste |
| `/keyword balkon,lift` | Whitelist: mind. 1 Begriff muss vorkommen |
| `/keyword` | Whitelist leeren |
| `/exclude studio,keller` | Ausschluss-Keywords |
| `/exclude` | Ausschluss leeren |
| `/verfuegbar 2026-09` | Nur Inserate verfügbar bis YYYY-MM |
| `/verfuegbar` | Verfügbarkeits-Filter leeren |
| `/pause` / `/resume` | Versand pausieren/fortsetzen |
| `/status` | Alle aktiven Filter |
| `/stats` | Statistiken (gesehen/beworben/besichtigung/…) |
| `/now` | Sofortiger Pipeline-Durchlauf (non-blocking) |

### Inserate
| Befehl | Wirkung |
|---|---|
| `/offen` | Offene Bewerbungen + Besichtigungen |
| `/liste` | Alle gemerkten Inserate |
| `/info <id>` | Details + Notiz + Status |
| `/notiz <id> <text>` | Freitext-Notiz speichern |
| `/beworben <id>` | Status: beworben |
| `/besichtigung <id>` | Status: Besichtigung vereinbart |
| `/abgelehnt <id>` | Status: abgelehnt |
| `/delete <id>` | Als erledigt markieren |
| `/cleanup` | Erledigte Inserate aus DB löschen |
| `/portale` | Integrierte Portale anzeigen |
| `/help` | Alle Befehle |

### Inline-Buttons (pro Inserat)
⭐ Merken · 📝 Entwurf · 📬 Beworben · ❌ Weg

## DB-Schema (listings-Tabelle relevante Felder)

```
id, source, url, title, price, price_num, rooms, space, location,
available, first_seen, last_seen, marked, marked_at, note
```

`marked` Werte: `interesting`, `beworben`, `besichtigung`, `abgelehnt`, `done`, `null`

## Filter-State (filter_state-Tabelle)

```
max_price, min_rooms, max_rooms, min_space, plz_list (JSON),
exclude_kw (JSON), kw_list (JSON), verfuegbar_ab (YYYY-MM),
paused, last_activity
```

## Infrastruktur

- VPS: `203.0.113.10`, User: `root`, Pfad: `/root/wohnungs-bot/`
- Deploy: Push auf `master` → GitHub Actions → SCP auf VPS → Services neu starten
- Services: `wohnungs-bot.service` (main --loop) + `wohnungs-bot-cmd.service` (command.py)
- `.env` auf VPS (nie ins Git): `WOHNUNGS_BOT_TOKEN`, IMAP-Zugangsdaten, `WOHNUNGS_DB`

## Anweisungen für Claude Code

**Style:** Python 3.10+, type hints wo sinnvoll, keine Frameworks,
Standard-Lib plus `requests` + `beautifulsoup4` + `pytest`. Funktionen
statt Klassen. Kommentare auf Deutsch ok.

**Filter-Prinzip:** Bei fehlenden/unparsbaren Feldern Inserat durchlassen
(lieber zu viel als zu wenig). Filter nur bei sicher erkannten Werten.

**Parser-Diagnose:** `python main.py --dump-emails` zeigt geparste
Listings direkt mit ✓/⚠-Flags pro Feld — wichtig nach Template-Änderungen
der Portale.

**Tests:** `python test_command.py` (kein pytest nötig, läuft direkt).
Mit pytest: `pytest test_command.py -v`.

## Nicht in Scope

- Keine Auto-Bewerbung (Empfänger nicht im Inserat, läuft übers Portal)
- Kein LLM-Call pro Inserat (deterministisches Template ist verlässlicher)
- Kein Scraping von Portal-Seiten direkt (Cloudflare — Suchabo-Mails sind der ToS-konforme Weg)
- Bot ist kein Mitdiskutant, reagiert nur auf Befehle
