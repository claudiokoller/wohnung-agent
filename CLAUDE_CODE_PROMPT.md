# Erster Prompt für Claude Code

Wenn du das Projekt in VS Code geöffnet hast, einfach diesen Block in
Claude Code reinkopieren als ersten Auftrag.

---

```
Lies zuerst CLAUDE.md vollständig — dort steht der Projekt-Kontext und
was du bauen sollst. Dann verschaff dir einen Überblick über die
bestehenden Module: main.py, config.py, db.py, sources.py,
email_source.py, application.py, notify.py.

Bau dann command.py wie in CLAUDE.md beschrieben:
- Telegram Long-Polling über getUpdates
- Whitelist auf TELEGRAM_CHAT_IDS aus config.py
- Filter-State + Merkliste in der bestehenden SQLite-DB (db.py
  erweitern, nicht parallel daneben)
- Alle Filter- und Inseratbefehle aus CLAUDE.md
- /help als Einstiegspunkt

Wichtig — Migration: Der Filter-State darf nicht doppelt in
config.SEARCH und der DB existieren. Beim ersten Start: Werte aus
config.SEARCH in die DB seeden, danach ist die DB führend.
sources.fetch_flatfox und email_source.fetch_email müssen den State
aus der DB lesen, nicht mehr direkt aus config.SEARCH.

Schreib während du baust kleine Unit-Tests für:
- Befehls-Parsing (/preis 2600 -> max_price=2600, /plz 8953,8957 ->
  Liste mit zwei PLZ)
- Filter-Match gegen Beispiel-Listings (PLZ-Match, Preis-Range,
  Exclude-Keyword)

Halt dich an den Stil der bestehenden Module: Funktionen statt
Klassen, Standard-Lib + requests + beautifulsoup4, keine Frameworks.

Stell Verständnisfragen, wenn was unklar ist. Bau nicht blind.
```

---

## Nachgelagerte Aufträge (nach command.py)

Wenn command.py läuft, in dieser Reihenfolge:

**Parser-Tuning gegen echte Mails:**
```
Ich hab unter ./email_dumps/ echte Portal-Alert-Mails gedumpt. Schau
dir je eine pro Portal an (Homegate, ImmoScout24, newhome) und prüf,
ob email_source.py die Felder Preis, Zimmer, m², PLZ und Ort korrekt
extrahiert. Pass die Regexe an wo nötig, ohne die Logik für die
anderen Portale zu brechen. Schreib einen kleinen Test, der gegen
diese Dumps läuft, damit künftige Template-Änderungen früh auffallen.
```

**Systemd-Services generieren:**
```
Generier mir zwei systemd-Service-Files für den Dauerbetrieb auf dem
VPS — einen für main.py --loop, einen für command.py. Mit Restart on
failure, EnvironmentFile für die Secrets, und einer kurzen
README-Sektion, wie ich sie installiere und enabled. User: meinusername,
Pfad: /home/meinusername/wohnungs-bot.
```

**Optional später — Health-Monitoring:**
```
Erweiter den Bot um ein simples Heartbeat-System: wenn 24h lang kein
einziges Inserat reinkam (egal von welcher Quelle), schick einen
Warnhinweis in die Gruppe. Verhindert, dass ein stiller Parser-Bruch
tagelang unbemerkt bleibt.
```
