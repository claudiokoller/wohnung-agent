# Wohnung Agent

Ein Telegram-Bot, der Mietinserate aus vier Schweizer Immobilienportalen in
einen gemeinsamen Chat bündelt: filtert nach eigenen Kriterien, erkennt
Inserate wieder, die auf mehreren Portalen stehen, und legt zu jedem Treffer
einen fertigen Bewerbungsentwurf dazu.

Gebaut für eine WG-Suche zu zweit in der Region Zürich, gelaufen von Ende
Mai bis Mitte September 2026 auf einem eigenen Server. Danach pausiert:
Wohnung gefunden.

[![Tests](https://github.com/claudiokoller/wohnung-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/claudiokoller/wohnung-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## Das Problem

Wer in Zürich sucht, hat Suchabos bei vier Portalen — und damit vier
Mailfluten, in denen dieselben Inserate mehrfach auftauchen und die
interessanten untergehen. Zu zweit suchen heisst zusätzlich: beide brauchen
denselben Stand.

Der Bot macht daraus einen Kanal, ohne Duplikate, mit Filtern, die sich per
Chat-Befehl ändern lassen.

## Was der Bot macht

1. **Abholen** – die Suchabo-Mails der vier Portale landen in einem eigenen
   Postfach und werden per IMAP abgeholt.
2. **Auslesen** – Preis, Zimmer, Fläche und Ort werden aus dem HTML der Mail
   geparst und in einen einheitlichen Datentyp überführt.
3. **Entdoppeln** – dasselbe Inserat kommt oft von drei Portalen; der Bot
   folgt den Weiterleitungslinks bis zur Inserat-Nummer und vergleicht
   hilfsweise Adresse, Preis, Zimmer und Fläche.
4. **Filtern und senden** – was den Kriterien entspricht, geht mit zwei
   Buttons in die Telegram-Gruppe. Die Filter liegen in SQLite und sind per
   Chat-Befehl änderbar.

## Beispiel

```
🏠 3.5 Zimmer Wohnung mit Balkon

📍 8004 Zürich
🚪 3.5 Zi  ·  📐 78 m²  ·  💰 2'450  ·  📊 31/m²
📅 ab 01.11.2026

🔗 https://www.flatfox.ch/de/flat/...
via flatfox · flatfox-123456

[ ⭐ Merken ]  [ 📝 Entwurf ]
```

## Architektur

```mermaid
flowchart LR
    P["4 Portale<br/>Suchabo-Mails"] --> M[(Postfach)]
    M --> B["Bot<br/>parsen · Duplikate raus · filtern"]
    B --> T(["Telegram-Gruppe"])
    B <--> D[("SQLite<br/>Inserate + Filter")]
    T -->|"/preis 2400"| B
```

Die Portale schicken ihre Suchabo-Mails an ein eigenes Postfach. Der Bot holt
sie per IMAP ab, liest Preis, Zimmer, Fläche und Ort aus dem HTML, wirft
bereits gesehene Inserate weg, prüft den Rest gegen die Filter und schickt
die Treffer in die Gruppe.

Mehr Details — Ablauf eines Durchlaufs, Datenmodell, Selbstüberwachung:
[docs/architecture.md](docs/architecture.md)

## Telegram-Befehle

| Bereich | Befehle |
|---|---|
| Filter | `/preis` · `/zimmer` · `/plz` (auch mit Gemeindenamen) · `/exclude` · `/pause` · `/resume` |
| Inserate | `/liste` · `/delete` · `/now` |
| Status | `/status` · `/stats` · `/portale` · `/help` |
| Betrieb | `/health` (Zustand pro Portal) · `/backup` |

Jedes Inserat kommt mit zwei Buttons: ⭐ Merken und 📝 Entwurf. Um 20:00 fasst
der Bot die gemerkten Inserate des Tages zusammen.

## Module

| Datei | Aufgabe |
|---|---|
| `main.py` | Ruft die Schritte nacheinander auf: `--seed`, `--loop`, `--dump-emails` |
| `email_source.py` | IMAP-Quelle: Alert-Mails → `Listing` |
| `sources.py` | `Listing`-Datentyp + Telegram-Formatierung |
| `db.py` | SQLite: Dedup, Filter, Merkliste, Mail-Log |
| `command.py` | Telegram-Befehle, Tagesübersicht |
| `notify.py` | Versand mit Inline-Buttons |
| `application.py` | Bewerbungsvorlage |
| `plz_lookup.py` | Postleitzahl ↔ Gemeindename, Kanton Zürich |
| `imap_auth.py` | Anmeldung am Postfach, unabhängig vom Anbieter |

## Setup

```bash
git clone https://github.com/claudiokoller/wohnung-agent.git
cd wohnung-agent
pip install -r requirements.txt

cp .env.example .env        # Bot-Token, IMAP-Zugang, DB-Pfad
# Suchkriterien und Profil in config.py anpassen

python main.py --seed       # Altinserate als gesehen markieren
python main.py --loop       # Dauerbetrieb, Poll alle 15 Minuten
python command.py           # Telegram-Befehle (zweiter Prozess)
```

Braucht Python 3.11+, ein eigenes Postfach für die Suchabos und einen
Telegram-Bot (via [@BotFather](https://t.me/BotFather)). Vollständige
Anleitung mit Postfach, Suchabos und systemd: [SETUP.md](SETUP.md).

## Tests

```bash
python test_command.py
```

60 Tests, ohne pytest und ohne Netzzugriff lauffähig.

## Designentscheide

- **Suchabo-Mails statt Scraping.** Die Portale sind hinter Cloudflare und
  verbieten Scraping. Ihre eigenen Mails liefern dieselben Treffer freiwillig.
- **Duplikate erkennen.** Dasselbe Inserat kommt oft von drei Portalen. Der
  Bot folgt den Weiterleitungslinks aus den Mails bis zur Inserat-Nummer des
  Portals. Geht das nicht, vergleicht er Adresse, Preis, Zimmer und Fläche.
- **Filter in der Datenbank statt im Code.** `/preis 2400` gilt sofort und
  übersteht Neustarts. Stünden die Werte zusätzlich im Code, hätte man zwei
  Stände, die auseinanderlaufen.
- **Bewerbungsentwurf ohne Sprachmodell.** Ein festes Textgerüst ist für
  einen Brief an einen Vermieter verlässlicher. Abgeschickt wird nichts
  automatisch — den Text kopiert man selbst ins Portalformular.

## Betrieb: was schiefging

Lehrreicher als das Bauen war der Betrieb danach. Drei Ausfälle, jeder
still — der Bot lief fehlerfrei weiter und schickte einfach nichts mehr:

| Ausfall | Ursache | Konsequenz im Code |
|---|---|---|
| Feed 9 Tage leer | Postfach-Quota voll, Provider wies Mails ab | Verarbeitete Mails werden gelöscht, nicht nur als gelesen markiert |
| Feed 2 Wochen leer | Mailkonto gesperrt, Login abgelehnt | Wiederholte Fehler melden statt sie endlos für vorübergehend zu halten |
| Feed leer trotz Mails | Suchabos breiter als der Bot-Filter | Verwerfungsgrund pro Inserat im Log |

Die Lehre: Ein Bot, der Nachrichten weiterleitet, meldet seinen eigenen
Ausfall nicht — Stille sieht aus wie „nichts Passendes dabei". Deshalb
überwacht er heute den Maileingang pro Portal und meldet sich selbst, wenn
eine Quelle verstummt.

## Lizenz

MIT — siehe [LICENSE](LICENSE). Profil und Suchkriterien in `config.py` sind
Platzhalter; echte Zugangsdaten gehören in `.env` und nie ins Repo.
