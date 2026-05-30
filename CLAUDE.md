# Wohnungs-Bot Zürich-Region — Projekt-Kontext für Claude Code

Diese Datei wird von Claude Code automatisch als Kontext geladen. Sie
beschreibt den Stand des Projekts und das, was noch zu tun ist.

## Was der Bot tut

Aggregiert Schweizer Mietinserate aus mehreren Portalen in einen
Telegram-Feed für eine 2er-WG-Suche. Architektur:

- **Flatfox** → offene JSON-API (direkter Poll)
- **Homegate / ImmoScout24 / newhome** → bewusst breite Suchabos auf
  einer Pseudo-Mailadresse; Bot pollt die per IMAP, parst die Mails

Beide Ströme münden in dieselbe Pipeline: `Listing`-Objekte,
SQLite-Dedup (auch cross-portal), Filterung, Telegram-Versand an eine
gemeinsame Gruppe (User + Kollege + Bot). Pro Treffer wird zusätzlich
ein fertiges WG-Bewerbungsanschreiben generiert (auf den Namen des
Kollegen, der vor Ort in CH ist).

## Modulübersicht (bereits gebaut)

| Datei | Aufgabe |
|---|---|
| `config.py` | Token, IMAP-Zugang, Suchkriterien (statisch), `APPLICANT`-Profil |
| `sources.py` | `fetch_flatfox(search, cfg)` + `Listing`-Datentyp |
| `email_source.py` | `fetch_email(search, cfg)` IMAP-Parser für Portal-Alert-Mails, plus `dump_emails()` zum Debuggen |
| `db.py` | SQLite-Dedup (`seen`-Tabelle) |
| `notify.py` | Telegram-Versand: `send(listing)` + `send_draft(subject, body)` |
| `application.py` | Bewerbungsvorlage (Sie-Form, CH-Konventionen) + optionaler IMAP-Draft-Append |
| `main.py` | Orchestrierung: `--seed`, `--loop`, `--dump-emails` |

## Was noch zu bauen ist: `command.py` (Command-Layer)

**Ziel:** Filter zentral per Telegram live steuern, statt `config.py`
von Hand zu editieren. Dies ist der eigentliche Mehrwert gegenüber
nativen Portal-Suchabos.

### Anforderungen

**Telegram Long-Polling** als separater Prozess (läuft parallel zu
`main.py --loop`). Verwendet `getUpdates` mit `offset`, kein Webhook.

**Whitelist** auf die gemeinsame Gruppen-Chat-ID. Alles andere wird
hart ignoriert (sonst kann jeder die Filter verstellen).

**Filter-State in SQLite** (neue Tabelle in der bestehenden DB,
einzeilig — Key/Value oder eine Zeile mit allen Feldern). Muss
Neustart überleben. Pipeline in `main.py`/`sources.py`/`email_source.py`
liest diesen State live bei jedem Durchlauf — *nicht* den statischen
`config.SEARCH`-Block.

**Befehle:**

Filterbefehle:
- `/preis <max>` — Maximalpreis CHF
- `/zimmer <min>-<max>` — z.B. `/zimmer 2.5-4`
- `/plz <liste>` — Komma-getrennte PLZ-Liste; leer = kein PLZ-Filter
  - `/plz add 8953` / `/plz del 8957` / `/plz` (zeigt aktuelle Liste)
- `/exclude <keywords>` — Komma-getrennt, Volltext-Match auf Titel/Body
- `/pause` / `/resume` — Treffer-Versand pausieren
- `/status` — aktuelle Filter + Pause-Status
- `/now` — sofortiger Pipeline-Durchlauf

Inseratbefehle (operieren auf gemerkten/zuletzt gesehenen Listings):
- `/info <id-oder-plz>` — Inseratsdaten erneut anzeigen
- `/bewirb <id-oder-plz>` — Bewerbungs-Entwurf erneut posten
- `/merk <id-oder-plz>` — als interessant markieren
- `/weg <id-oder-plz>` — als erledigt/uninteressant markieren

Hilfe: `/help` listet alle Befehle.

### Filter-Wirkung

- **Flatfox-Quelle:** Filter-State wird in die API-Query gemappt (Preis,
  Zimmer; PLZ-Liste über mehrere `location`-Parameter oder
  Nachfiltern, je nachdem was Flatfox stabiler unterstützt).
- **Mail-Quelle:** Parser läuft wie gehabt, Filter wird *nach* dem
  Parsing angewendet (Preis/Zimmer numerisch, PLZ exakter Match,
  Exclude als case-insensitive substring auf Titel+Block).
- Bei fehlenden Feldern (Parser konnte nichts extrahieren): Inserat
  durchlassen, nicht filtern. Lieber zu viel als zu wenig.

### Inserat-Adressierung

Inserate haben stabile IDs wie `flatfox-998877` oder `homegate-4001234567`.
Zusätzlich PLZ als bequemer Kurz-Adressat (gibt's mehrere Treffer mit
derselben PLZ: jüngsten nehmen oder nachfragen — Designentscheidung beim
Bauen). `db.seen` um Felder `last_seen` (timestamp), `marked`
(`interesting`/`done`/null), `title`, `price`, `location` erweitern oder
neue Tabelle `listings` daneben.

## Setup-Stand (was der User bereits vorbereitet hat)

Der User richtet die Infra alleine ein:
- Pseudo-Gmail mit App-Passwort
- Telegram-Bot via @BotFather
- Gemeinsame Telegram-Gruppe (User + Kollege + Bot)
- Portal-Suchabos auf der Pseudo-Adresse
- Hosting auf kleinem VPS (User ist auf Reisen, kein Laptop-Betrieb)

Der Kollege liefert nur sein Profil (Name, Beruf, Telefon, private
Gmail). Er wird der Hauptbewerber sein, der User ist zweite WG-Person.

## Anweisungen für Claude Code

**Konvention:** ehrlich bleiben. Wenn etwas nicht sauber baubar ist
(z.B. Portal-X hat keine API), das sagen statt einen brittlen Scraper
zu bauen. Pragmatische Defaults, klar dokumentiert.

**Style:** wie der bestehende Code — Python 3.10+, type hints wo
sinnvoll, keine Frameworks (kein FastAPI, kein Celery), Standard-Lib
plus `requests` + `beautifulsoup4`. Funktionen statt Klassen, wo es
geht. Kommentare auf Deutsch sind ok, passt zum Rest.

**Erst lesen, dann bauen:** vor dem Bau von `command.py` einmal die
bestehenden Module durchlesen (vor allem `main.py`, `db.py`,
`sources.py`), damit die neue Logik sauber andockt. Filter-State darf
nicht doppelt in `config.SEARCH` und DB existieren — Migration: bei
erstem Start der Werte aus `config.SEARCH` in die DB seeden, danach DB
führend.

**Testen ohne echten Telegram-Account:** Long-Polling lässt sich mit
einem Mock-`getUpdates`-Server testen, oder einfach ein Trockenlauf,
der Befehle aus einer JSON-Datei einliest. Bevorzugt: kleine
Unit-Tests für das Befehls-Parsing (`/preis 2600` →
`{"max_price": 2600}`) und für den Filter-Match (gegen
Beispiel-`Listing`s).

## Nicht in Scope

- Keine Auto-Bewerbung (Empfänger steht nie im Inserat, läuft übers
  Portalformular — bewusste Designentscheidung, mit dem User
  besprochen).
- Kein LLM-Call pro Inserat. Bewerbungstext ist deterministisch per
  Template, ist verlässlicher und schneller.
- Kein Scraping von Homegate/ImmoScout24-Listings direkt (Cloudflare).
  Suchabo-Mails sind der ToS-konforme Weg.
- Bot ist kein Mitdiskutant in der Gruppe. Reagiert nur auf Befehle,
  ignoriert alles andere.
