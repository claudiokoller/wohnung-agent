"""
Bewerbungs-/Besichtigungsanfrage.

Erzeugt aus einem Listing + deinem Profil (config.APPLICANT) einen
fertigen, auf das konkrete Inserat zugeschnittenen Anschreiben-Entwurf
(Standard-Deutsch, Sie-Form, CH-Konventionen).

Bewusst deterministisch per Template statt LLM pro Inserat: CH-
Mietbewerbungen sind formelhaft, ein sauberes Template ist
verlässlicher und schneller als generierter Text.

Versand ist NICHT automatisch: die Empfänger-Adresse steckt fast nie
im Inserat (Portal-Kontaktformular). Der Entwurf geht copy-paste-fertig
per Telegram raus; optional zusätzlich als echter Entwurf ins Postfach.
"""
import email.utils
import imaplib
import time
from email.message import EmailMessage


def _household_sentence(p):
    occ = p.get("occupation", "berufstätig")
    status = p.get("employer_or_status", "")
    base = f"ich bin {occ}" + (f", {status}" if status else "")
    flags = []
    if p.get("nichtraucher", True):
        flags.append("Nichtraucher")
    if p.get("keine_haustiere", True):
        flags.append("keine Haustiere")
    if p.get("ruhig", True):
        flags.append("ruhig und zuverlässig")
    tail = (", " + ", ".join(flags)) if flags else ""

    hh = p.get("household", "single")
    if hh == "paar":
        return (f"Wir sind ein berufstätiges Paar ({base}){tail}. "
                f"Die Wohnung möchten wir als 2-Personen-Haushalt mieten.")
    if hh == "wg":
        return (f"Wir sind zwei berufstätige Personen ({base}){tail}, "
                f"die die Wohnung gemeinsam als 2-Personen-Haushalt "
                f"mieten möchten – beide mit geregeltem Einkommen und "
                f"vollständigem Dossier.")
    if hh == "familie":
        return (f"Wir sind eine Familie ({base}){tail}, "
                f"mit geregeltem Einkommen und vollständigem Dossier.")
    return (f"Zu mir: {base}{tail}. "
            f"Die Wohnung würde ich allein bewohnen.")


def build_letter(listing, cfg):
    """-> (subject, body) als reiner Text."""
    p = cfg.APPLICANT
    names = p.get("names", "<Name>")
    phone = p.get("phone", "<Telefon>")
    mail = p.get("email", "<E-Mail>")
    move_in = p.get("move_in", "per sofort oder nach Vereinbarung")

    subject = f"Bewerbung / Besichtigungsanfrage – {listing.title}, {listing.location}"

    solo = p.get("household", "single") == "single"
    poss = "meinen Vorstellungen" if solo else "unseren Vorstellungen"

    extra = p.get("extra_line", "").strip()
    extra_block = f"\n\n{extra}" if extra else ""

    body = (
        "Sehr geehrte Damen und Herren\n\n"
        f"mit grossem Interesse habe ich Ihr Inserat für die "
        f"{listing.rooms}-Zimmer-Wohnung an der {listing.location} "
        f"({listing.price}) gesehen. Die Wohnung entspricht genau "
        f"{poss} und ich würde sie sehr gerne besichtigen.\n\n"
        f"{_household_sentence(p)}\n\n"
        f"Als gewünschten Bezugstermin sehe ich {move_in} vor. Ein "
        f"vollständiges Bewerbungsdossier (Ausweiskopie, aktueller "
        f"Betreibungsauszug, Einkommens-/Lohnnachweis) stelle ich Ihnen "
        f"gerne vorab digital oder bei der Besichtigung zu."
        f"{extra_block}\n\n"
        f"Über einen Besichtigungstermin würde ich mich sehr freuen. "
        f"Sie erreichen mich unter {phone} oder {mail}.\n\n"
        f"Freundliche Grüsse\n"
        f"{names}"
    )
    return subject, body


def build_blank_letter(listing, cfg) -> tuple[str, str]:
    """Leere Struktur zum manuellen Ausfüllen.
    Gibt (subject, body) zurück — body enthält nur Rahmen, kein Fliesstext."""
    p = cfg.APPLICANT
    names = p.get("names", "<Name>")
    phone = p.get("phone", "<Telefon>")
    mail  = p.get("email", "<E-Mail>")

    subject = f"Bewerbung / Besichtigungsanfrage – {listing.title}, {listing.location}"

    body = (
        "Sehr geehrte Damen und Herren\n\n"
        "[Hier eigenen Text schreiben]\n\n"
        f"Sie erreichen mich unter {phone} oder {mail}.\n\n"
        f"Freundliche Grüsse\n"
        f"{names}"
    )
    return subject, body


def save_file(listing, subject, body, out_dir="drafts"):
    import os
    import re

    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^\w]+", "_", listing.id)[:60]
    path = os.path.join(out_dir, f"{safe}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Betreff: {subject}\n\n{body}\n")
    return path


def imap_append_draft(listing, subject, body, cfg):
    """Legt den Entwurf als echte Draft-Mail ins Postfach (To leer ->
    Empfänger aus dem Portal-Kontaktformular ergänzen). Best effort."""
    msg = EmailMessage()
    msg["From"] = cfg.APPLICANT.get("email", cfg.IMAP_USER)
    msg["To"] = ""  # bewusst leer: Portal gibt keine Adresse her
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(
        body + f"\n\n---\nInserat: {listing.url}\n(via {listing.source})"
    )
    try:
        M = imaplib.IMAP4_SSL(cfg.IMAP_HOST, cfg.IMAP_PORT)
        M.login(cfg.IMAP_USER, cfg.IMAP_PASS)
        M.append(
            cfg.DRAFT_IMAP_FOLDER,
            "(\\Draft)",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        M.logout()
        return True
    except Exception as e:
        print(f"Draft-Append fehlgeschlagen ({cfg.DRAFT_IMAP_FOLDER}): {e}")
        return False
