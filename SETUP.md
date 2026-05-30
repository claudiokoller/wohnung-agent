# Setup-Checkliste

Schritt für Schritt, in dieser Reihenfolge. Hak ab, was erledigt ist.

## Phase 1 — Vorbereitung (alleine machbar, parallel zu Phase 2)

- [ ] **Pseudo-Gmail anlegen** (z.B. `wohnung.suchen@example.com`)
  - 2FA aktivieren (Pflicht für App-Passwort)
  - App-Passwort generieren: Account → Sicherheit → 2-Schritt-Verifizierung → App-Passwörter
  - Passwort sicher notieren (16 Zeichen ohne Leerzeichen)
- [ ] **Telegram-Bot erstellen**
  - `@BotFather` anschreiben → `/newbot`
  - Name + Username vergeben → Token notieren
- [ ] **Telegram-Gruppe erstellen**
  - Kollege einladen
  - Bot einladen (Username eingeben)
  - In der Gruppe `/start` schreiben
  - Gruppen-Chat-ID holen: kurz `@RawDataBot` in die Gruppe holen, Chat-ID auslesen (beginnt mit `-100…`), Bot wieder entfernen
- [ ] **VPS aufsetzen** (z.B. Hetzner CX11, Ubuntu 24.04)
  - SSH-Zugang testen
  - Python 3.10+ prüfen: `python3 --version`
  - Optional: `tmux` installieren für persistente Sessions

## Phase 2 — Daten vom Kollegen einholen

- [ ] Vollständiger Name
- [ ] Beruf + Anstellungs-Status (z.B. "Softwareentwickler, festangestellt bei X")
- [ ] **Private Gmail-Adresse** (Absender im Anschreiben)
- [ ] Telefonnummer (CH-Format)
- [ ] Gewünschter Bezugstermin
- [ ] Such-Kriterien grob: Preisrahmen, Zimmer-Range, Region (für die Portal-Suchabos)
- [ ] Entscheidung: Draft-Ablage in seinem privaten Gmail aktivieren?
  - Falls ja: braucht App-Passwort *seines* privaten Accounts
  - Falls nein: Entwurf landet nur im Telegram-Chat, er kopiert von Hand

## Phase 3 — Portal-Suchabos einrichten (alle auf die Pseudo-Adresse)

Wichtig: **bewusst breit**. Filtern macht der Bot.

- [ ] **Homegate** — Suchabo mit Pseudo-Mail
- [ ] **ImmoScout24** — Suchabo mit Pseudo-Mail
- [ ] **newhome** — Suchabo mit Pseudo-Mail
- [ ] **Flatfox** — Suchabo nur optional (Bot pollt eh direkt die API)

## Phase 4 — Gmail-Filter im Pseudo-Postfach

- [ ] Filter erstellen: Absender enthält `homegate.ch` OR `immoscout24.ch` OR `newhome.ch` OR `flatfox.ch`
- [ ] Aktion: Label `wohnung` zuweisen, Posteingang überspringen
- [ ] Test: eine Mail von Hand reinwerfen → landet im Label

## Phase 5 — Code deployen

- [ ] Projekt-Ordner auf VPS hochladen (`scp` oder Git)
- [ ] `python3 -m venv .venv && source .venv/bin/activate`
- [ ] `pip install -r requirements.txt`
- [ ] `config.py` ausfüllen:
  - `TELEGRAM_BOT_TOKEN` (aus Phase 1)
  - `TELEGRAM_CHAT_IDS = ["-100…"]` (Gruppen-ID, einzelner Eintrag reicht)
  - IMAP-Block: Pseudo-Gmail + App-Passwort, `IMAP_FOLDER = "wohnung"`
  - `APPLICANT`-Block: Kollegen-Daten aus Phase 2, `household = "wg"`, deinen Namen mit reinnehmen
  - Suchkriterien grob — werden später per Telegram-Befehl überschrieben

## Phase 6 — Parser tunen

- [ ] `python main.py --dump-emails` ausführen
- [ ] HTML-Dumps unter `./email_dumps/` durchschauen
- [ ] Pro Portal eine Mail prüfen: extrahiert der Parser Preis, Zimmer, PLZ korrekt?
- [ ] Falls nein: Regexe in `email_source.py` anpassen (mit Claude Code)
- [ ] Erfolg = Probelauf zeigt sinnvolle Treffer

## Phase 7 — Seed + erster Live-Lauf

- [ ] `python main.py --seed` → markiert alle aktuellen Inserate als "gesehen"
- [ ] `python main.py` einmalig (sollte 0 neue Treffer melden)
- [ ] Warte 1–2 Tage, sammle echte neue Inserate
- [ ] `python main.py --loop` startet Dauerschleife
- [ ] In tmux laufen lassen oder als systemd-Service einrichten (siehe unten)

## Phase 8 — Command-Layer (`command.py`) bauen

Hier kommt Claude Code in VS Code ins Spiel. Siehe `CLAUDE.md` für den
Auftrag. Nach dem Bau:

- [ ] `python command.py` als zweiter Prozess parallel zu `main.py --loop`
- [ ] In der Gruppe `/help` → Befehlsliste muss kommen
- [ ] `/plz 8953,8957` → setzen, `/status` → bestätigen
- [ ] `/now` → sofortiger Durchlauf, prüfen ob Filter greift

## Phase 9 — Dauerbetrieb absichern

Zwei systemd-Services auf dem VPS:

```ini
# /etc/systemd/system/wohnungs-bot.service
[Unit]
Description=Wohnungs-Bot Polling
After=network.target

[Service]
WorkingDirectory=/home/USER/wohnungs-bot
ExecStart=/home/USER/wohnungs-bot/.venv/bin/python main.py --loop
Restart=always
RestartSec=30
EnvironmentFile=/home/USER/wohnungs-bot/.env

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/wohnungs-bot-cmd.service
[Unit]
Description=Wohnungs-Bot Command Layer
After=network.target

[Service]
WorkingDirectory=/home/USER/wohnungs-bot
ExecStart=/home/USER/wohnungs-bot/.venv/bin/python command.py
Restart=always
RestartSec=30
EnvironmentFile=/home/USER/wohnungs-bot/.env

[Install]
WantedBy=multi-user.target
```

- [ ] `.env` mit allen Tokens/Passwörtern (nicht ins Git!)
- [ ] `systemctl enable --now wohnungs-bot wohnungs-bot-cmd`
- [ ] Logs prüfen: `journalctl -u wohnungs-bot -f`

## Phase 10 — Im Betrieb

- Erste Woche breit laufen lassen, Treffer beobachten
- Per `/plz`, `/preis`, `/exclude` schrittweise eingrenzen
- Bei Parser-Fehlern (Portal ändert Template): erneut `--dump-emails`,
  Regex nachziehen
- Monatlich kurz Logs checken
