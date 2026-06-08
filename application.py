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
    hh = p.get("household", "single")
    flags = []
    if p.get("nichtraucher", True):
        flags.append("Nichtraucher")
    if p.get("keine_haustiere", True):
        flags.append("keine Haustiere")
    if p.get("ruhig", True):
        flags.append("ruhig und gepflegt wohnend")
    flags_str = (" Wir sind " + ", ".join(flags) + ".") if flags else ""

    if hh == "wg":
        age       = p.get("age", "")
        p1_name   = p.get("person1_name", "")
        p1_occ    = p.get("occupation", "")
        p1_status = p.get("employer_or_status", "")
        p2_name   = p.get("person2_name", "")
        p2_occ    = p.get("person2_occupation", "")
        p2_status = p.get("person2_status", "")

        age_str = f", beide {age} Jahre alt" if age else ""
        p1_detail = (f" und ist {p1_status}" if p1_status else "")
        p2_detail = (f" und ist {p2_status}" if p2_status else "")

        flags_sentence = ""
        if flags:
            parts = []
            if p.get("nichtraucher"):
                parts.append("Nichtraucher")
            if p.get("keine_haustiere"):
                parts.append("haben keine Haustiere")
            if p.get("ruhig"):
                parts.append("wohnen ruhig und gepflegt")
            flags_sentence = " Wir sind " + ", ".join(parts) + "."

        return (
            f"Zu uns: Wir sind zwei Personen{age_str}, die gemeinsam eine Wohnung suchen. "
            f"{p1_name} hat {p1_occ}{p1_detail}. "
            f"{p2_name} studiert {p2_occ}{p2_detail}."
            f"{flags_sentence}"
        )

    occ = p.get("occupation", "berufstätig")
    status = p.get("employer_or_status", "")
    base = f"ich bin {occ}" + (f", {status}" if status else "")
    solo_flags = (", " + ", ".join(flags)) if flags else ""
    if hh == "paar":
        return (f"Wir sind ein berufstätiges Paar ({base}){solo_flags}. "
                f"Die Wohnung möchten wir als 2-Personen-Haushalt mieten.")
    if hh == "familie":
        return (f"Wir sind eine Familie ({base}){solo_flags}, "
                f"mit geregeltem Einkommen und vollständigem Dossier.")
    return (f"Zu mir: {base}{solo_flags}. "
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
    subj    = "ich"   if solo else "wir"
    haben   = "habe"  if solo else "haben"
    wurde   = "würde" if solo else "würden"
    sehe    = "sehe"  if solo else "sehen"
    stelle  = "stelle" if solo else "stellen"
    mich    = "mich"  if solo else "uns"
    poss    = "meinen Vorstellungen" if solo else "unseren Vorstellungen"

    extra = p.get("extra_line", "").strip()
    extra_block = f"\n\n{extra}" if extra else ""

    body = (
        "Sehr geehrte Damen und Herren\n\n"
        f"mit grossem Interesse {haben} {subj} Ihr Inserat für die "
        f"{listing.rooms}-Zimmer-Wohnung in {listing.location} "
        f"({listing.price}) gesehen. Die Wohnung entspricht genau "
        f"{poss} und {subj} {wurde} sie sehr gerne besichtigen.\n\n"
        f"{_household_sentence(p)}\n\n"
        f"Als gewünschten Bezugstermin {sehe} {subj} {move_in} vor. Ein "
        f"vollständiges Bewerbungsdossier (Ausweiskopie, aktueller "
        f"Betreibungsauszug, Einkommens-/Lohnnachweis) {stelle} {subj} Ihnen "
        f"gerne vorab digital oder bei der Besichtigung zu."
        f"{extra_block}\n\n"
        f"Über einen Besichtigungstermin {wurde} {subj} {mich} sehr freuen. "
        f"Sie erreichen {mich} unter {phone} oder {mail}.\n\n"
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
        import gmail_oauth
        M = imaplib.IMAP4_SSL(cfg.IMAP_HOST, cfg.IMAP_PORT)
        gmail_oauth.imap_login(M, cfg)   # XOAUTH2 falls konfiguriert, sonst App-Passwort
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
