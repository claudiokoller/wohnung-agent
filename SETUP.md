# Setup

Von null bis Dauerbetrieb. Die Kurzfassung steht im [README](README.md) —
hier die vollständige Reihenfolge inklusive Postfach, Suchabos und systemd.

Voraussetzungen: Python 3.10+, ein IMAP-Postfach, ein Telegram-Konto. Für den
Dauerbetrieb ein kleiner Server (ein 5-Euro-VPS reicht).

## 1. Telegram vorbereiten

- [ ] Bot erstellen: `@BotFather` anschreiben → `/newbot` → Token notieren
- [ ] Gruppe erstellen und den Bot hineinholen (praktischer als Einzelchats,
      wenn zu zweit gesucht wird)
- [ ] In der Gruppe `/start` schreiben
- [ ] Gruppen-Chat-ID auslesen: kurz `@RawDataBot` in die Gruppe holen, die
      negative ID notieren, Bot wieder entfernen

## 2. Postfach für die Portal-Mails

Ein **eigenes Postfach nur für den Bot** — nicht das private. Er löscht
verarbeitete Mails, und die Suchabos produzieren viel Volumen.

- [ ] Postfach bei einem Anbieter anlegen, der IMAP-Logins aus Rechenzentren
      zulässt. Grosse Freemail-Anbieter blocken Logins von Server-IPs gern
      dauerhaft — das war in diesem Projekt die häufigste Ausfallursache.
- [ ] IMAP aktivieren, Zugangsdaten notieren (Host, Port 993, Benutzer,
      Passwort bzw. App-Passwort)
- [ ] Auf genügend Speicher achten. Läuft das Postfach in die Quota, weist der
      Anbieter eingehende Mails ab und der Bot bekommt nichts mehr, ohne dass
      ein Fehler auftritt.

Optional: Statt Passwort geht OAuth2/XOAUTH2 — `imap_auth.py` erkennt das
automatisch, sobald die drei `WOHNUNGS_OAUTH_*`-Variablen gesetzt sind. Den
Refresh-Token holt `python oauth_setup.py`.

## 3. Suchabos auf den Portalen

Bei Homegate, ImmoScout24, newhome und Flatfox je ein Suchabo mit der
Bot-Adresse als Empfänger.

- [ ] Abos **bewusst breit** fassen — das Feintuning macht der Bot, und dessen
      Filter lassen sich per Chat-Befehl ändern, die Abos nicht.
- [ ] Bestätigungs-/Aktivierungsmails zeitnah anklicken. Läuft der Bot schon,
      löscht er sie beim nächsten Durchlauf mit den Alert-Mails weg.
- [ ] Nach ein, zwei Tagen prüfen, ob von **jedem** Portal Mails ankommen.
      Ein Abo, das nie bestätigt wurde, schweigt einfach.

## 4. Installation

```bash
git clone https://github.com/claudiokoller/wohnung-agent.git
cd wohnung-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 5. Konfiguration

Zugangsdaten kommen in `.env` und gehören nie ins Git:

```bash
cp .env.example .env
```

| Variable | Inhalt |
|---|---|
| `WOHNUNGS_BOT_TOKEN` | Token aus Schritt 1 |
| `WOHNUNGS_IMAP_HOST` / `_PORT` | IMAP-Server, üblicherweise Port 993 |
| `WOHNUNGS_IMAP_USER` / `_PASS` | Zugangsdaten aus Schritt 2 |
| `WOHNUNGS_IMAP_FOLDER` | `INBOX`, oder ein eigener Ordner bei serverseitiger Sortierung |
| `WOHNUNGS_DB` | absoluter Pfad zur SQLite-Datei |

In `config.py` kommen die nicht-geheimen Dinge:

- [ ] `TELEGRAM_CHAT_IDS` — die Gruppen-ID aus Schritt 1
- [ ] `APPLICANT` — Name, Beruf, Telefon, Bezugstermin, `household`
      (`single` / `paar` / `wg` / `familie`); daraus baut der Bot den
      Bewerbungsentwurf
- [ ] `SEARCH` — Startwerte für die Suchkriterien. Sie seeden nur den ersten
      Start; danach führt die Datenbank, geändert wird per Telegram-Befehl

## 6. Probelauf

```bash
python main.py --dump-emails   # zeigt geparste Inserate mit Flags pro Feld
python main.py --seed          # markiert alles Vorhandene als gesehen
python main.py                 # ein Durchlauf, sollte 0 neue Treffer melden
```

`--dump-emails` ist die Parser-Diagnose: Es markiert nichts als gelesen,
löscht nichts und sendet nichts. Sieht ein Feld falsch aus, sind die Regexe
in `email_source.py` die Stellschraube — Portale ändern ihre Mail-Templates.

Dann den Command-Layer testen:

```bash
python command.py
```

In der Gruppe `/help` → die Befehlsliste muss kommen. `/plz 8953,8957`
setzen, mit `/status` bestätigen, `/now` löst einen sofortigen Durchlauf aus.

## 7. Dauerbetrieb mit systemd

Zwei Services, weil die beiden Prozesse unterschiedlich ticken: Die Pipeline
schläft zwischen den Durchläufen, das Long-Polling hängt dauerhaft an der
Telegram-API.

```ini
# /etc/systemd/system/wohnung-agent.service
[Unit]
Description=Wohnung-Agent Polling
After=network.target

[Service]
WorkingDirectory=/opt/wohnung-agent
ExecStart=/opt/wohnung-agent/.venv/bin/python main.py --loop
Restart=always
RestartSec=30
EnvironmentFile=/opt/wohnung-agent/.env

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/wohnung-agent-cmd.service
[Unit]
Description=Wohnung-Agent Command Layer
After=network.target

[Service]
WorkingDirectory=/opt/wohnung-agent
ExecStart=/opt/wohnung-agent/.venv/bin/python command.py
Restart=always
RestartSec=30
EnvironmentFile=/opt/wohnung-agent/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now wohnung-agent wohnung-agent-cmd
journalctl -u wohnung-agent -f
```

Optional automatisches Deployment: `.github/workflows/deploy.yml` kopiert den
Code per SCP auf den Server und startet die Services neu. Der Workflow ist
bewusst nur manuell auslösbar und braucht die Repository-Secrets
`SSH_PRIVATE_KEY`, `SSH_HOST` und `SSH_USER`.

## 8. Im Betrieb

- Erste Woche breit laufen lassen, dann per `/preis`, `/plz`, `/zimmer` und
  `/exclude` schrittweise eingrenzen.
- Kommt nichts mehr, **nicht** sofort den Code verdächtigen. Die Reihenfolge:
  Kommen überhaupt noch Mails an (`/health` zeigt pro Portal, wie viele Mails
  in 24 Stunden kamen und wann die letzte eintraf)?
  Passen die Suchabos noch zum Filter? Erst dann der Parser.
- Der Bot überwacht sich selbst: Er meldet sich, wenn insgesamt oder von einer
  einzelnen Quelle über längere Zeit keine Mail mehr eintrifft, und eskaliert
  wiederholte Login-Fehler statt sie endlos als vorübergehend zu behandeln.
