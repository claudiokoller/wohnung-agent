import os

# --- Telegram ---
# Token von @BotFather. Lieber als Env-Variable setzen statt hier hardcoden.
TELEGRAM_BOT_TOKEN = os.getenv("WOHNUNGS_BOT_TOKEN", "DEIN_BOT_TOKEN")

# Du + Kollege: beide Chat-IDs eintragen.
# Alternative: gemeinsame Gruppe erstellen, Bot reinholen, dann nur die
# Gruppen-Chat-ID (negativ, z.B. -100123...) hier reintun.
TELEGRAM_CHAT_IDS = [
    "-1001234567890",        # Gruppe "Bot Wohnungssuche ZH"
]

# --- Suchkriterien ---
SEARCH = {
    "min_price": 0,
    "max_price": 2500,        # CHF / Monat (Bruttomiete)
    "min_rooms": 2.5,
    "max_rooms": 4.5,
    "min_space": 50,          # m²
    # Bounding Box grob um Zürich-Stadt. Verfeiner sie nach Bedarf
    # (z.B. enger auf Kreis 4/5, oder weiter Richtung Zugersee).
    "bbox": {
        "south": 47.32,
        "west":  8.44,
        "north": 47.43,
        "east":  8.62,
    },
}

# --- IMAP: Portal-Alert-Mails (Homegate / ImmoScout24 / newhome / Flatfox) ---
# Du richtest auf den Portalen normale Suchabos ein -> die Treffer-Mails
# werden hier per IMAP geholt und in dieselbe Pipeline gespeist.
# Gmail: App-Passwort nötig (normales Passwort geht bei IMAP nicht).
# Empfehlung: Gmail-Filter -> Label "wohnung", dann IMAP_FOLDER = "wohnung".
IMAP_HOST   = os.getenv("WOHNUNGS_IMAP_HOST", "imap.gmail.com")
IMAP_PORT   = int(os.getenv("WOHNUNGS_IMAP_PORT", "993"))
IMAP_USER   = os.getenv("WOHNUNGS_IMAP_USER", "deinmail@example.com")
IMAP_PASS   = os.getenv("WOHNUNGS_IMAP_PASS", "APP_PASSWORT")
IMAP_FOLDER = os.getenv("WOHNUNGS_IMAP_FOLDER", "INBOX")

# False = verarbeitete Mails als gelesen markieren (verhindert Doppel-
#         verarbeitung). True = Mails unangetastet lassen (nur Dedup via DB).
IMAP_KEEP_UNREAD = False

# Tracking-Redirects in den Mails auflösen, um die echte Portal-Listing-ID
# (= stabiler Dedup-Key) zu bekommen. Aus = Fallback auf Inhalts-Fingerprint.
RESOLVE_TRACKING_LINKS = True

# Minuten zwischen den Durchläufen im --loop Modus
POLL_INTERVAL_MIN = 15

DB_PATH = os.getenv("WOHNUNGS_DB", "listings.db")

# --- Bewerbungs-/Besichtigungsanfrage --------------------------------------
# Wird bei jedem neuen Treffer als fertiger Entwurf erzeugt.
APPLICANT = {
    "names": "Max <Nachname>",        # bei WG/Paar beide Namen
    "household": "single",                # single | paar | wg | familie
    "occupation": "Softwareentwickler",
    "employer_or_status": "festangestellt", # z.B. "festangestellt bei X" / "selbständig"
    "phone": "+41 7x xxx xx xx",
    "email": "deinmail@example.com",      # eure Kontaktadresse fürs Anschreiben
    "move_in": "per sofort oder nach Vereinbarung",
    "nichtraucher": True,
    "keine_haustiere": True,
    "ruhig": True,
    "extra_line": "",                     # optionaler Zusatzsatz, frei
}

DRAFT_IN_TELEGRAM   = True   # Entwurf als kopierfertige Telegram-Nachricht
DRAFT_SAVE_FILES    = False  # Entwurf zusätzlich als .txt nach ./drafts/
DRAFT_IMAP_APPEND   = False  # Entwurf als echte Draft-Mail ins Postfach legen
# Provider-spezifisch: Gmail "[Gmail]/Drafts", GMX "Entwürfe" o.ä.
DRAFT_IMAP_FOLDER   = os.getenv("WOHNUNGS_DRAFT_FOLDER", "[Gmail]/Drafts")
