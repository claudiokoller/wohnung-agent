"""
IMAP-Quelle.

Idee: auf Homegate / ImmoScout24 / newhome / Flatfox normale Suchabos
einrichten. Deren Treffer-Mails landen in einem Postfach (am besten via
Filter in ein eigenes Label). Dieses Modul holt die ungelesenen
Portal-Mails per IMAP, parst die enthaltenen Inserate und gibt sie als
Listing-Objekte zurück -> gleiche Dedup-/Telegram-Pipeline wie Flatfox.

Wichtig / ehrlich: die HTML-Templates der Portale ändern sich und die
Links sind oft Klick-Tracking-Redirects. Die Muster unten sind
brauchbare Defaults, aber verifizier sie einmal mit

    python main.py --dump-emails

gegen je eine echte Alert-Mail pro Portal und pass LISTING_PATTERNS /
die Regexe bei Bedarf an. Der IMAP-/Parsing-Unterbau ist robust, das
Feintuning ist portalspezifisch.
"""
import email
import hashlib
import imaplib
import re
import socket
from email.header import decode_header

# IMAP-Verbindung hängt ohne Timeout ewig bei Netzwerkproblemen
socket.setdefaulttimeout(60)

import requests
from bs4 import BeautifulSoup

from sources import Listing

# Absender-Domains der Portal-Alert-Mails
ALERT_SENDER_DOMAINS = [
    "homegate.ch",
    "immoscout24.ch",
    "immostreet.ch",
    "newhome.ch",
    "flatfox.ch",
]

# Erkennung Portal + Listing-ID aus der Ziel-URL (nach Redirect-Auflösung)
LISTING_PATTERNS = {
    "Homegate": re.compile(
        r"homegate\.ch/(?:de/|fr/|it/|en/)?(?:mieten|rent|kaufen|buy)/(\d{5,})"
    ),
    "ImmoScout24": re.compile(
        r"immoscout24\.ch/(?:de/|fr/|it/|en/)?d/[\w/-]*?(\d{6,})"
    ),
    "newhome": re.compile(
        r"newhome\.ch/(?:de/|fr/|it/)?[\w/-]*?(\d{6,})"
    ),
    "Flatfox": re.compile(
        r"flatfox\.ch/(?:de/|fr/|it/|en/)?flat/(\d+)"
    ),
}

PRICE_RE = re.compile(r"CHF\s*[\d’’.,]+")
AVAILABLE_RE = re.compile(
    r"(?:ab sofort"
    r"|ab\s+\d{1,2}[\.\s]\s*\w+[\s,]+\d{4}"
    r"|(?:ab|per)\s+\d{1,2}\.\d{1,2}\.\d{2,4}"
    r"|ab\s+(?:januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember"
    r"|january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{4})",
    re.I
)
ROOMS_RE = re.compile(r"([\d]+(?:[.,]\d)?)\s*(?:Zimmer|Zi\.?|rooms?|bedrooms?|pièces|locali)", re.I)
SPACE_RE = re.compile(r"([\d’’.,]+)\s*m²")
LOC_RE = re.compile(
    r"(?:\b(?P<plz>\d{4})\s+(?P<city>[A-ZÄÖÜ][\wÄÖÜäöüéèà.\-]{1,25})"
    r"|\b(?P<city2>[A-ZÄÖÜ][\wÄÖÜäöüéèà.\-]{1,25})\s*\((?P<plz2>\d{4})\))"
)
# Generische Button-/Link-Texte in Portal-Mails – kein brauchbarer Titel
_CTA_RE = re.compile(
    r"^(zum inserat|zur anzeige|mehr erfahren|details?( anzeigen)?|"
    r"anzeige ansehen|zur wohnung|jetzt ansehen|zum objekt|"
    r"mehr details?|inserat ansehen|view listing|more details?|"
    r"weiter|hier klicken|jetzt anzeigen|alle details|"
    r"zur immobilie|kontaktieren|kontakt aufnehmen|anzeige|inserat)$",
    re.I,
)

_UA = {"User-Agent": "Mozilla/5.0 (compatible; WohnungsBot/1.0)"}


# --- IMAP-Helfer -----------------------------------------------------------

def _decode(s):
    if not s:
        return ""
    out = ""
    for text, enc in decode_header(s):
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="replace")
        else:
            out += text
    return out


def _html_body(msg):
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition") or ""):
                continue
            if part.get_content_type() != "text/html":
                continue
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
    elif msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            html = payload.decode(charset, errors="replace")
    return html


def _connect(cfg):
    M = imaplib.IMAP4_SSL(cfg.IMAP_HOST, cfg.IMAP_PORT)
    M.login(cfg.IMAP_USER, cfg.IMAP_PASS)
    typ, data = M.select(cfg.IMAP_FOLDER)
    if typ != "OK":
        M.logout()
        raise RuntimeError(
            f"IMAP-Ordner «{cfg.IMAP_FOLDER}» nicht gefunden. "
            f"Gmail-Label prüfen oder WOHNUNGS_IMAP_FOLDER anpassen."
        )
    return M


def _alert_uids(M):
    uids = []
    for dom in ALERT_SENDER_DOMAINS:
        typ, data = M.uid("search", None, "UNSEEN", "FROM", dom)
        if typ == "OK" and data and data[0]:
            uids.extend(data[0].split())
    # Reihenfolge stabil, Duplikate raus
    return list(dict.fromkeys(uids))


# --- Parsing ---------------------------------------------------------------

def _resolve(href, enabled):
    """Tracking-Redirect auf die echte Listing-URL auflösen."""
    if not enabled:
        return href
    try:
        r = requests.head(href, allow_redirects=True, timeout=8, headers=_UA)
        return r.url or href
    except Exception:
        try:
            r = requests.get(href, allow_redirects=True, timeout=10,
                              headers=_UA, stream=True)
            return r.url or href
        except Exception:
            return href


def _identify(url):
    for portal, pat in LISTING_PATTERNS.items():
        m = pat.search(url)
        if m:
            return portal, m.group(1), url
    return None, None, url


def _first(rx, text):
    m = rx.search(text)
    return m.group(0) if m else None


def _location(text):
    m = LOC_RE.search(text)
    if not m:
        return "—"
    plz  = m.group("plz")  or m.group("plz2")
    city = (m.group("city") or m.group("city2") or "").strip()
    return f"{plz} {city}"


def _fingerprint(portal, title, block):
    h = hashlib.sha1(f"{portal}|{title}|{block}".encode()).hexdigest()[:16]
    return f"{portal.lower()}-fp-{h}"


def _best_block(a, hard_limit: int = 800) -> tuple[str, str | None]:
    """Steigt in der DOM-Hierarchie auf, bis ein Block mit Preis/Zimmer/Ort
    gefunden wird. Behebt das Problem wenn der Link in einer isolierten
    Button-<td> sitzt und der Wohnungskontext eine Ebene höher liegt.
    Gibt (block_text, heading_oder_None) zurück."""
    node = a
    best = a.get_text(" ", strip=True)
    heading = None

    for _ in range(6):
        p = getattr(node, "parent", None)
        if p is None or getattr(p, "name", None) in (None, "[document]", "body", "html"):
            break
        node = p

        text = re.sub(r"\s+", " ", " ".join(p.stripped_strings)).strip()
        if len(text) > hard_limit * 3:
            break  # zu gross -> mehrere Listings oder ganze Mail drin

        best = text

        if not heading:
            for tag in ("h1", "h2", "h3", "h4", "strong"):
                h = p.find(tag)
                if h:
                    t = h.get_text(" ", strip=True)
                    if 5 < len(t) < 150:
                        heading = t
                        break

        has_price = bool(PRICE_RE.search(text))
        has_rooms = bool(ROOMS_RE.search(text))
        has_loc   = bool(LOC_RE.search(text))
        if (has_price or has_rooms) and has_loc:
            break

    return best[:hard_limit], heading


def _candidate_links(soup):
    """Links, die plausibel auf ein Inserat zeigen (direkt ODER Tracking)."""
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue
        low = href.lower()
        is_listing = any(p.search(low) for p in LISTING_PATTERNS.values())
        is_trackish = any(d in low for d in
                          ("homegate", "immoscout", "newhome", "flatfox",
                           "click", "track", "link", "redirect", "url="))
        if is_listing or is_trackish:
            out.append(a)
    return out


def _parse_html(html, resolve_links):
    soup = BeautifulSoup(html, "html.parser")
    seen_local = set()
    listings = []

    for a in _candidate_links(soup):
        href = a["href"].strip()
        portal, lid, final_url = _identify(href)

        if not portal:  # evtl. Tracking-Wrapper -> auflösen und nochmal
            resolved = _resolve(href, resolve_links)
            portal, lid, final_url = _identify(resolved)
            if not portal:
                continue

        link_text = a.get_text(" ", strip=True)
        block, heading = _best_block(a)
        # CTA-Link-Text ("Zum Inserat" etc.) ist kein Titel → Überschrift oder Blockbeginn
        if _CTA_RE.match(link_text.strip()):
            title = (heading or block[:80] or "Wohnung")[:120]
        else:
            title = (link_text or heading or block[:80] or "Wohnung")[:120]

        rooms = ROOMS_RE.search(block)
        space = SPACE_RE.search(block)
        avail = AVAILABLE_RE.search(block)

        listing_id = f"{portal}-{lid}" if lid else \
            _fingerprint(portal, title, block)
        if listing_id in seen_local:
            continue
        seen_local.add(listing_id)

        listings.append(Listing(
            id=listing_id,
            source=portal,
            title=title,
            price=_first(PRICE_RE, block) or "?",
            rooms=rooms.group(1) if rooms else "?",
            space=space.group(1) if space else "?",
            location=_location(block),
            url=final_url,
            available=avail.group(0).strip() if avail else None,
        ))
    return listings


# --- Öffentliche Source-Funktion -------------------------------------------

def fetch_email(search, cfg):
    """Holt Portal-Alert-Mails via IMAP -> list[Listing].

    Kein Nachfiltern nach Preis/Zimmer: das Suchabo auf dem Portal hat
    bereits nach euren Kriterien gefiltert. Erneutes Filtern würde wegen
    Parsing-Lücken eher gute Treffer verwerfen.
    """
    M = _connect(cfg)
    listings = []
    try:
        for uid in _alert_uids(M):
            typ, data = M.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not data or not data[0]:
                continue
            msg = email.message_from_bytes(data[0][1])
            html = _html_body(msg)
            if html:
                listings.extend(
                    _parse_html(html, cfg.RESOLVE_TRACKING_LINKS)
                )
            if not cfg.IMAP_KEEP_UNREAD:
                M.uid("store", uid, "+FLAGS", "(\\Seen)")
        M.close()
    finally:
        M.logout()
    return listings


def dump_emails(cfg, out_dir="email_dumps"):
    """Debug: rohe HTML-Bodies der Portal-Mails speichern und geparste
    Listings direkt ausgeben — ohne Link-Auflösung für Geschwindigkeit.
    Markiert nichts als gelesen."""
    import os

    os.makedirs(out_dir, exist_ok=True)
    M = _connect(cfg)
    n = 0
    try:
        for uid in _alert_uids(M):
            typ, data = M.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not data[0]:
                continue
            msg = email.message_from_bytes(data[0][1])
            sender  = _decode(msg.get("From", "unknown"))
            subject = _decode(msg.get("Subject", ""))
            html    = _html_body(msg)
            if not html:
                continue
            safe = re.sub(r"[^\w]+", "_", sender)[:40]
            path = os.path.join(out_dir, f"{uid.decode()}_{safe}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            n += 1

            print(f"\n{'='*60}")
            print(f"Von:     {sender[:70]}")
            print(f"Betreff: {subject[:70]}")
            print(f"Gespeichert: {path}")

            # Geparste Listings direkt anzeigen (ohne Redirect-Auflösung)
            listings = _parse_html(html, resolve_links=False)
            if not listings:
                print("  ⚠️  Keine Listings gefunden — Parser-Tuning nötig!")
            for l in listings:
                ok_title = "✓" if l.title and l.title not in ("Wohnung",) else "⚠"
                ok_price = "✓" if l.price != "?" else "⚠"
                ok_rooms = "✓" if l.rooms != "?" else "⚠"
                ok_loc   = "✓" if l.location != "—" else "⚠"
                print(
                    f"  [{l.id}]\n"
                    f"    {ok_title} Titel:   {l.title}\n"
                    f"    {ok_price} Preis:   {l.price}\n"
                    f"    {ok_rooms} Zimmer:  {l.rooms}  📐 {l.space}\n"
                    f"    {ok_loc} Ort:     {l.location}\n"
                    f"    🔗 {l.url[:80]}"
                )
        M.close()
    finally:
        M.logout()
    print(f"\n{n} Mail(s) nach ./{out_dir}/ gedumpt.")
