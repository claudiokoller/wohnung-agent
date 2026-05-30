# Wohnungs-Bot Zürich

Pollt neue Mietinserate, filtert nach euren Kriterien, dedupliziert über
SQLite und schickt neue Treffer per Telegram an dich + deinen Kollegen.

## Setup

1. **Bot erstellen**: bei `@BotFather` `/newbot`, Token kopieren.
2. **Chat-IDs holen**: du + Kollege schreiben dem Bot je eine Nachricht,
   `@userinfobot` gibt die numerische Chat-ID zurück. Beide in
   `config.py` -> `TELEGRAM_CHAT_IDS` eintragen.
   (Tipp: gemeinsame Gruppe + Bot reinholen ist oft praktischer als zwei IDs.)
3. **Token setzen**:
   ```bash
   export WOHNUNGS_BOT_TOKEN="123456:ABC..."
   ```
4. **Suchabos auf den Portalen** einrichten (Homegate, ImmoScout24,
   newhome, Flatfox) mit euren Kriterien -> die schicken Treffer-Mails.
5. **IMAP konfigurieren** (für die Portal-Mails):
   ```bash
   export WOHNUNGS_IMAP_USER="deinmail@gmail.com"
   export WOHNUNGS_IMAP_PASS="<app-passwort>"
   ```
   - Gmail braucht ein **App-Passwort** (Account -> Sicherheit ->
     App-Passwörter), das normale Passwort geht bei IMAP nicht.
   - Empfehlung: Gmail-Filter -> alle Portal-Mails in ein Label
     `wohnung`, dann `WOHNUNGS_IMAP_FOLDER=wohnung`. Hält die INBOX
     sauber und die Suche schnell.
6. **Kriterien anpassen** in `config.py` (Preis, Zimmer, m², Bounding Box –
   gilt für Flatfox; die Mail-Treffer sind schon portalseitig gefiltert).
7. **Installieren & seeden**:
   ```bash
   pip install -r requirements.txt
   python main.py --seed     # markiert Altinserate als gesehen, sendet nichts
   ```
8. **Laufen lassen** – Dauerschleife:
   ```bash
   python main.py --loop
   ```
   ...oder per cron alle 15 Min (sauberer für nen Server):
   ```
   */15 * * * * cd /pfad/wohnungs-bot && python main.py
   ```

## IMAP-Parser tunen

Die Portal-HTML-Templates ändern sich und Links sind oft
Tracking-Redirects. Die Parser in `email_source.py` sind brauchbare
Defaults – verifizier sie einmal gegen echte Mails:

```bash
python main.py --dump-emails   # speichert rohe HTML-Bodies nach ./email_dumps/
```

Dann je eine Mail pro Portal anschauen und bei Bedarf `LISTING_PATTERNS`
bzw. die Preis-/Zimmer-Regexe anpassen. `--dump-emails` markiert nichts
als gelesen und sendet nichts.

## Architektur

- `config.py`       – Telegram + Suchkriterien + IMAP + Bewerber-Profil
- `sources.py`      – Flatfox-API-Quelle + `Listing`-Datentyp
- `email_source.py` – IMAP-Quelle (Portal-Alert-Mails -> `Listing`)
- `application.py`  – CH-Bewerbungsvorlage + optionaler Postfach-Entwurf
- `db.py`           – SQLite-Dedup
- `notify.py`       – Telegram-Versand (Treffer + Entwurf)
- `main.py`         – Orchestrierung (einmal / `--loop` / `--seed` /
  `--dump-emails`)

## Bewerbungs-Entwurf

Bei jedem neuen Treffer baut der Bot aus `config.APPLICANT` ein
fertiges Anschreiben (Standard-Deutsch, Sie-Form, CH-Konventionen,
Hinweis aufs vollständige Dossier) und schickt es als
kopierfertige Telegram-Nachricht direkt hinter dem Inserat.

Profil einmal in `config.py` -> `APPLICANT` ausfüllen
(`household`: `single` / `paar` / `wg` / `familie` bestimmt die
Formulierung). Zusätzliche Ausgabewege per Flags:

- `DRAFT_IN_TELEGRAM` – kopierfertig im Chat (Default an)
- `DRAFT_SAVE_FILES`  – zusätzlich als `.txt` nach `./drafts/`
- `DRAFT_IMAP_APPEND` – als echter Entwurf ins Postfach
  (`DRAFT_IMAP_FOLDER` provider-spezifisch: Gmail `[Gmail]/Drafts`,
  GMX `Entwürfe`)

**Wichtig:** kein Auto-Versand. Die Empfänger-Adresse steht fast nie
im Inserat – Kontakt läuft über das Portal-Kontaktformular. Der Bot
liefert den passgenauen Entwurf, einfügen/abschicken machst du.

Jede Quelle ist `(search, cfg) -> list[Listing]` und wird in
`main.SOURCES` registriert. Beide Quellen laufen durch denselben
Dedup-/Telegram-Pfad, Cross-Portal-Duplikate werden über die
Listing-ID rausgefiltert.

## Quellen erweitern

`fetch_flatfox` (offene JSON-API) und `fetch_email` (IMAP-Suchabos)
decken zusammen Flatfox + Homegate + ImmoScout24 + newhome ab, ohne
Cloudflare-Bot-Schutz umgehen zu müssen. Weitere Quelle = neue
Funktion `(search, cfg) -> list[Listing]`, dann in `main.SOURCES`
ergänzen.

Falls Flatfox das Feld-Mapping ändert: rohe JSON-Response loggen und
`fetch_flatfox` anpassen. Falls eine Portal-Mail nicht sauber geparst
wird: `--dump-emails` und `LISTING_PATTERNS` in `email_source.py`
nachziehen.

## Hinweis

Höfliches Poll-Intervall lassen (15 Min reicht für Wohnungssuche
locker). Der Weg über Suchabo-Mails ist bewusst gewählt: ToS-konform,
kein Bot-Schutz-Problem, keine Scraper-Wartung – die Portale liefern
die Treffer selbst, der Bot bündelt sie nur für euch beide.
