# Wohnungs-Bot Zürich-Region — Projekt-Kontext für Claude Code

Diese Datei wird von Claude Code automatisch als Kontext geladen.

## Was der Bot tut

Aggregiert Schweizer Mietinserate aus mehreren Portalen in einen
Telegram-Feed für eine 2er-WG-Suche (Max + Sam). Architektur:

- **Homegate / ImmoScout24 / newhome / Flatfox** → Suchabos auf
  ein dediziertes IMAP-Postfach (Zugangsdaten in `.env`); Bot pollt
  per IMAP, parst die Mails
- Pipeline: `Listing`-Objekte → SQLite-Dedup (inkl. Cross-Portal) →
  Filterung → Telegram-Versand an die konfigurierte Gruppe

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
| `test_command.py` | Unit-Tests (50 Tests, `python test_command.py`) |

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
| `/health` | Zustand: letzter Poll, empfangen/geparst, **Mail-Eingang pro Portal**, letztes Inserat, offene Resends, letztes Backup, DB-Größe |
| `/backup` | Konsistentes lokales DB-Backup jetzt erstellen (mit Integritätscheck) |
| `/help` | Alle Befehle |

### Inline-Buttons (pro Inserat)
⭐ Merken · 📝 Entwurf · 📬 Beworben · ❌ Weg

## DB-Schema (listings-Tabelle relevante Felder)

```
id, source, url, title, price, price_num, rooms, space, location,
available, first_seen, last_seen, marked, marked_at, note
```

`marked` Werte: `interesting`, `beworben`, `besichtigung`, `abgelehnt`, `done`, `null`

## Überwachung (mail_log-Tabelle + Wächter in main.py)

`mail_log (portal, ts, mails)` protokolliert, welches Portal wann Alert-Mails
geliefert hat. Darauf setzen drei Dinge auf:

- **Heartbeat** (`_check_heartbeat`): warnt nach 24h ohne Mail-Eingang von
  irgendeinem Portal. Bezugsgröße ist der Mail-EINGANG — früher hing er an
  `last_activity`, das bei jedem erfolgreichen Poll neu gesetzt wird und
  deshalb auch bei totem Zufluss frisch aussah (24 Tage stiller Ausfall im
  Juli/August 2026).
- **Quellen-Wächter** (`_check_sources`): meldet einzelne Portale, die seit
  >72h stumm sind, während andere liefern — typisch für ein deaktiviertes
  oder nie bestätigtes Suchabo. Schweigt, wenn alle stumm sind (das ist der
  Heartbeat-Fall). Überwacht werden nur `email_source.MONITORED_PORTALS`.
- **Täglicher Bericht** (`command._maybe_send_monitor_report`, 08:00 Zürich):
  Statuszeile pro Portal (Mails/24h, letzte Mail), Mail-Eingang gesamt,
  letztes Inserat. Räumt dabei `mail_log` älter als 90 Tage weg.

`monitor_since` (meta) ist der Referenzzeitpunkt, ab dem Stille zählt — ohne
ihn bliebe eine frische Installation ohne je empfangene Mail alarmfrei.

## Filter-State (filter_state-Tabelle)

```
max_price, min_rooms, max_rooms, min_space, plz_list (JSON),
exclude_kw (JSON), kw_list (JSON), verfuegbar_ab (YYYY-MM),
paused, last_activity
```

## Infrastruktur

- VPS (Ubuntu), Pfad `/root/wohnungs-bot/` — Host/User liegen in den GitHub-Secrets, nicht im Repo
- Deploy: GitHub Actions (manuell ausgelöst) → Tests → SCP auf VPS → Services neu starten
- Services: `wohnungs-bot.service` (main --loop) + `wohnungs-bot-cmd.service` (command.py)
- `.env` auf VPS (nie ins Git): `WOHNUNGS_BOT_TOKEN`, IMAP-Zugangsdaten, `WOHNUNGS_DB`

## Anweisungen für Claude Code

**Style:** Python 3.10+, type hints wo sinnvoll, keine Frameworks,
Standard-Lib plus `requests` + `beautifulsoup4` + `pytest`. Funktionen
statt Klassen. Kommentare auf Deutsch ok.

**Filter-Prinzip:** Bei fehlenden/unparsbaren Feldern Inserat durchlassen
(lieber zu viel als zu wenig). Filter nur bei sicher erkannten Werten.
**Ausnahme — PLZ ist HART:** Die PLZ ist das wichtigste Kriterium und wird
strikt durchgesetzt. Bei gesetzter `plz_list` wird ein Inserat OHNE erkannte
PLZ aus der Liste **verworfen** (nicht durchgelassen). Diese Regel nicht
aufweichen — der Parser liest die PLZ zuverlässig.

**Parser-Diagnose:** `python main.py --dump-emails` zeigt geparste
Listings direkt mit ✓/⚠-Flags pro Feld — wichtig nach Template-Änderungen
der Portale. Nur brauchbar, solange die Mails noch im Postfach liegen — der
Bot löscht sie im Durchlauf, in dem er sie verarbeitet.

**Filter-Diagnose:** `db.filter_reason()` gibt zu jedem verworfenen Inserat
`(Kategorie, Grund)` zurück; `apply_filter(..., verbose=True)` (so ruft
`main.py` es auf) schreibt das ins Log:

```
Filter: Flatfox "3.5 Zimmer Wohnung" 8004 Zürich CHF 2100 — PLZ 8004 nicht in Liste
Filter: 5 von 6 verworfen (2× PLZ, 1× Fläche, 1× Preis, 1× Zimmer).
Durchlauf fertig. 1 neue Inserate gesendet (5 gefiltert).
```

Grund: verworfene Inserate hinterlassen sonst KEINE Spur — `upsert_listing`
läuft erst nach `apply_filter`, und die Mail ist im selben Durchlauf gelöscht.
Ohne das Log sieht «Filter zu eng», «Suchabo zu breit» und «Parser gebrochen»
im Journal identisch aus (alle drei: `0 neue Inserate gesendet`). Beim
Nachschauen zählt die Verteilung: lauter fremde PLZ ⇒ Abo im Portal falsch
eingestellt; viele knapp über `max_price` ⇒ Bot-Filter enger als der Markt.

**Tests:** `python test_command.py` (kein pytest nötig, läuft direkt).
Mit pytest: `pytest test_command.py -v`.

## Nicht in Scope

- Keine Auto-Bewerbung (Empfänger nicht im Inserat, läuft übers Portal)
- Kein LLM-Call pro Inserat (deterministisches Template ist verlässlicher)
- Kein Scraping von Portal-Seiten direkt (Cloudflare — Suchabo-Mails sind der ToS-konforme Weg)
- Bot ist kein Mitdiskutant, reagiert nur auf Befehle
