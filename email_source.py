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
import time
from concurrent.futures import ThreadPoolExecutor
from email.header import decode_header

# IMAP-Verbindung hängt ohne Timeout ewig bei Netzwerkproblemen
socket.setdefaulttimeout(60)

import requests
from bs4 import BeautifulSoup

import db
import imap_auth
from sources import Listing

# Diagnose des letzten fetch_email-Laufs (von main.py gelesen, um einen stillen
# Parser-/Template-Bruch zu erkennen: Mails kommen an, aber 0 Inserate geparst).
LAST_RUN = {"fetched": 0, "parsed": 0, "per_source": {}}

# Nach so vielen 0-Treffer-Durchläufen wird eine Mail aufgegeben (gelöscht).
# Schützt echte Inserat-Mails über ein Reparatur-Fenster hinweg, ohne
# Bestätigungs-/Nicht-Inserat-Mails endlos neu zu verarbeiten.
_MAX_PARSE_ATTEMPTS = 5

# Transiente IMAP-Fehler (mailbox.org/Heinlein drosselt zu schnelle Logins:
# «[UNAVAILABLE] Temporary authentication failure»). Kein toter Login, sondern
# kurz erneut versuchen.
_TRANSIENT_RE = re.compile(
    r"UNAVAILABLE|Temporary (?:authentication )?failure|temporar|try again|"
    r"timed out|timeout|connection reset|EOF",
    re.I,
)


def is_transient_error(e) -> bool:
    """True bei vorübergehendem IMAP-/Netzfehler (Retry sinnvoll, kein Alarm)."""
    return bool(_TRANSIENT_RE.search(str(e)))

# Absender-Domain -> Portalname. Die Zuordnung erlaubt es, den Mail-Eingang
# pro Quelle zu protokollieren; nur so fällt ein einzelnes totes Suchabo auf,
# während die übrigen Portale weiterliefern.
SENDER_PORTALS = {
    "homegate.ch":    "Homegate",
    "immoscout24.ch": "ImmoScout24",
    "immostreet.ch":  "ImmoStreet",
    "newhome.ch":     "newhome",
    "flatfox.ch":     "Flatfox",
}

# Absender-Domains der Portal-Alert-Mails
ALERT_SENDER_DOMAINS = list(SENDER_PORTALS)

# Portale, für die tatsächlich ein Suchabo besteht und deren Stille daher ein
# Alarm ist. Bewusst enger als SENDER_PORTALS: ImmoStreet wird als Absender
# noch akzeptiert, ohne dass sein Fehlen als Ausfall gemeldet wird.
MONITORED_PORTALS = ("Homegate", "ImmoScout24", "newhome", "Flatfox")

# Erkennung Portal + Listing-ID aus der Ziel-URL (nach Redirect-Auflösung)
LISTING_PATTERNS = {
    "Homegate": re.compile(
        r"homegate\.ch/(?:de/|fr/|it/|en/)?(?:mieten|rent|kaufen|buy)/(\d{5,})"
    ),
    "ImmoScout24": re.compile(
        r"immoscout24\.ch/(?:de/|fr/|it/|en/)?(?:d/[\w/-]*?|rent/|mieten/|louer/|affittare/)(\d{5,})"
    ),
    "newhome": re.compile(
        # Echtes Listing-Format ist newhome.ch/id/<id> — eng halten, sonst
        # matchen suchabo/verlaengern/loeschen-Links (Zahlen aus der UUID).
        r"newhome\.ch/(?:de/|fr/|it/)?id/(\d{5,})"
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
ROOMS_RE = re.compile(r"([\d]+(?:[.,]\d)?)[\s-]*(?:Zimmer|Zi\.?|rooms?|bedrooms?|pièces|locali)", re.I)
SPACE_RE = re.compile(r"([\d’’.,]+)\s*m\s?[²2]")
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
    r"zur immobilie|kontaktieren|kontakt aufnehmen|anzeige|inserat|"
    r"anbieter kontaktieren\s*[›»]?|contact the advertiser|"
    r"alle treffer anschauen|view all matching listings|"
    r"suchabo bearbeiten|suchabo löschen|edit search alert|delete search alert|"
    r"abmelden|unsubscribe)$",
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


def _connect(cfg, attempts: int = 3):
    """Öffnet die IMAP-Verbindung und meldet sich an. Bei transienten Fehlern
    (Drossel/Netz) bis zu `attempts`-mal mit Backoff erneut versuchen — ein
    einzelner «Temporary authentication failure» soll keinen Durchlauf killen."""
    last = None
    for i in range(attempts):
        M = None
        try:
            M = imaplib.IMAP4_SSL(cfg.IMAP_HOST, cfg.IMAP_PORT)
            imap_auth.imap_login(M, cfg)   # XOAUTH2 falls konfiguriert, sonst Passwort
            typ, data = M.select(cfg.IMAP_FOLDER)
            if typ != "OK":
                raise RuntimeError(
                    f"IMAP-Ordner «{cfg.IMAP_FOLDER}» nicht gefunden. "
                    f"WOHNUNGS_IMAP_FOLDER prüfen."
                )
            return M
        except Exception as e:
            last = e
            if M is not None:
                try:
                    M.logout()
                except Exception:
                    pass
            if i < attempts - 1 and is_transient_error(e):
                wait = 5 * (i + 1)   # 5s, 10s
                print(f"IMAP transient ({e}) — Retry {i + 1}/{attempts - 1} in {wait}s")
                time.sleep(wait)
                continue
            raise
    raise last   # nicht erreichbar, aber explizit


def _alert_uids_by_portal(M, unseen_only=True) -> dict[str, list]:
    """UIDs der Alert-Mails, gruppiert nach Portal. Eine UID wird nur einem
    Portal zugeordnet (erster Treffer gewinnt), damit die Eingangszählung
    keine Mail doppelt wertet."""
    out: dict[str, list] = {}
    taken: set = set()
    criteria = ["UNSEEN", "FROM"] if unseen_only else ["FROM"]
    for dom, portal in SENDER_PORTALS.items():
        typ, data = M.uid("search", None, *criteria, dom)
        if typ != "OK" or not data or not data[0]:
            continue
        fresh = [u for u in data[0].split() if u not in taken]
        taken.update(fresh)
        if fresh:
            out.setdefault(portal, []).extend(fresh)
    return out


def _alert_uids(M, unseen_only=True):
    uids = []
    for portal_uids in _alert_uids_by_portal(M, unseen_only).values():
        uids.extend(portal_uids)
    return uids


# --- Parsing ---------------------------------------------------------------

# Tracking-/Redirect-Hosts: NIE die Listing-ID aus dem Tracking-Link raten,
# sondern erst auflösen und aus dem aufgelösten Ziel identifizieren. Sonst
# matcht z.B. newhomes Versand-Host r.mailing.newhome.ch (enthält "newhome.ch")
# fälschlich als Listing — mit zufälliger ID aus dem Token und kaputtem Link.
_TRACKER_RE = re.compile(
    r"r\.mailing\.|\.sendgrid\.net|/tr/cl/|/ls/click|/uni/ls/click|/redirect", re.I
)


def _resolve(href, enabled):
    """Tracking-Redirect auf die echte Listing-URL auflösen.

    Erst HEAD (schnell). Bleibt das Ergebnis ein Tracking-Link (manche Versand-
    Server wie newhomes Brevo leiten nur bei GET um), GET nachschieben."""
    if not enabled:
        return href
    try:
        r = requests.head(href, allow_redirects=True, timeout=8, headers=_UA)
        url = r.url or href
        if not _TRACKER_RE.search(url):
            return url   # HEAD hat aufgelöst
    except Exception:
        pass
    try:
        r = requests.get(href, allow_redirects=True, timeout=10,
                          headers=_UA, stream=True)
        return r.url or href
    except Exception:
        return href


def _identify(url):
    if _TRACKER_RE.search(url):
        return None, None, url   # Tracking-Link -> erst auflösen lassen
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


def _best_block(a, max_container: int = 4000) -> tuple[str, str | None]:
    """Steigt in der DOM-Hierarchie auf, bis ein Block mit Preis/Zimmer/Ort
    gefunden wird. Gibt (block_text, heading_oder_None) zurück — block_text
    untrunkiert, damit Regex-Matching auch bei langen Listing-Texten greift."""
    node = a
    best = a.get_text(" ", strip=True)
    heading = None

    for _ in range(6):
        p = getattr(node, "parent", None)
        if p is None or getattr(p, "name", None) in (None, "[document]", "body", "html"):
            break
        node = p

        text = re.sub(r"\s+", " ", " ".join(p.stripped_strings)).strip()
        if len(text) > max_container:
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

    return best, heading


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


def _data_score(price, rooms, space, location) -> int:
    """Zählt wie viele Felder sinnvoll gefüllt sind (höher = besser)."""
    return sum(v not in ("?", "—", "", None) for v in [price, rooms, space, location])


def _parse_html(html, resolve_links):
    soup = BeautifulSoup(html, "html.parser")
    seen_idx: dict[str, int] = {}   # listing_id -> Index in `listings`
    listings: list = []

    candidates = _candidate_links(soup)
    if not candidates:
        return listings

    # Pass 1: direkt erkennbare URLs identifizieren, Tracking-URLs sammeln
    href_map: dict[str, tuple] = {}   # href -> (portal, lid, final_url)
    to_resolve: set[str] = set()
    for a in candidates:
        href = a["href"].strip()
        if href in href_map or href in to_resolve:
            continue
        portal, lid, final_url = _identify(href)
        if portal:
            href_map[href] = (portal, lid, final_url)
        elif resolve_links:
            to_resolve.add(href)
        else:
            href_map[href] = (None, None, href)

    # Pass 2: Tracking-Links parallel auflösen (statt sequenziell je ~3s)
    if to_resolve:
        hrefs = list(to_resolve)
        with ThreadPoolExecutor(max_workers=min(8, len(hrefs))) as ex:
            resolved_urls = list(ex.map(lambda h: _resolve(h, True), hrefs))
        for href, resolved in zip(hrefs, resolved_urls):
            portal, lid, _ = _identify(resolved)
            href_map[href] = (portal, lid, resolved if portal else href)

    # Pass 3: Listings parsen — bessere Daten überschreiben leere Ersterfassung
    for a in candidates:
        href = a["href"].strip()
        portal, lid, final_url = href_map.get(href, (None, None, href))
        if not portal:
            continue

        link_text = a.get_text(" ", strip=True)
        block, heading = _best_block(a)
        # CTA-Link-Text ("Zum Inserat" etc.) ist kein Titel → Überschrift oder Blockbeginn
        if _CTA_RE.match(link_text.strip()):
            title = (heading or block[:80] or "Wohnung")[:120]
        else:
            title = (link_text or heading or block[:80] or "Wohnung")[:120]

        price    = _first(PRICE_RE, block) or "?"
        rooms    = ROOMS_RE.search(block)
        space    = SPACE_RE.search(block)
        avail    = AVAILABLE_RE.search(block)
        location = _location(block)

        rooms_val = rooms.group(1) if rooms else "?"
        space_val = space.group(1) if space else "?"
        avail_val = avail.group(0).strip() if avail else None

        listing_id = f"{portal}-{lid}" if lid else _fingerprint(portal, title, block)

        if listing_id in seen_idx:
            # Nur überschreiben wenn aktuelle Daten reichhaltiger sind
            idx   = seen_idx[listing_id]
            old   = listings[idx]
            old_s = _data_score(old.price, old.rooms, old.space, old.location)
            new_s = _data_score(price, rooms_val, space_val, location)
            if new_s > old_s:
                listings[idx] = Listing(
                    id=listing_id, source=portal,
                    title=title if title != "Wohnung" else old.title,
                    price=price, rooms=rooms_val, space=space_val,
                    location=location, url=final_url,
                    available=avail_val or old.available,
                )
            continue

        seen_idx[listing_id] = len(listings)
        listings.append(Listing(
            id=listing_id, source=portal, title=title,
            price=price, rooms=rooms_val, space=space_val,
            location=location, url=final_url, available=avail_val,
        ))

    # Pass 4: Listings mit score=0 aus dem besten nicht-identifizierten Link befüllen.
    # Nötig wenn Portal-ID aus einem Bild-Link stammt (z.B. newhome) der in einer
    # eigenen Tabellenzeile sitzt — Daten stehen in einem Schwester-Link.
    low_quality = [(lid_k, idx_v) for lid_k, idx_v in seen_idx.items()
                   if _data_score(listings[idx_v].price, listings[idx_v].rooms,
                                  listings[idx_v].space, listings[idx_v].location) == 0]
    if low_quality:
        best_s = 0
        best_d = None
        for a in candidates:
            href = a["href"].strip()
            if href_map.get(href, (None,))[0] is not None:
                continue  # wurde in Pass 3 als Listing verarbeitet
            blk, hdg = _best_block(a)
            lt = a.get_text(" ", strip=True)
            if _CTA_RE.match(lt.strip()):
                t_c = (hdg or blk[:80] or "")[:120]
            else:
                t_c = (lt or hdg or blk[:80] or "")[:120]
            p_c = _first(PRICE_RE, blk) or "?"
            rm  = ROOMS_RE.search(blk)
            sp  = SPACE_RE.search(blk)
            av  = AVAILABLE_RE.search(blk)
            lc  = _location(blk)
            rv, sv, avv = (rm.group(1) if rm else "?"), (sp.group(1) if sp else "?"), (av.group(0).strip() if av else None)
            sc = _data_score(p_c, rv, sv, lc)
            if sc > best_s:
                best_s, best_d = sc, (t_c, p_c, rv, sv, lc, avv)
        if best_d:
            t_c, p_c, rv, sv, lc, avv = best_d
            for _, idx_v in low_quality:
                old = listings[idx_v]
                listings[idx_v] = Listing(
                    id=old.id, source=old.source,
                    title=t_c or old.title,
                    price=p_c, rooms=rv, space=sv,
                    location=lc, url=old.url,
                    available=avv or old.available,
                )

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
    fetched = 0
    try:
        # 1. Erst alle Mails parsen, Ergebnis pro UID merken. Welche als gelesen
        #    markiert werden, entscheiden wir DANACH — so geht bei einem Template-
        #    Bruch (0 Inserate aus allen Mails) nichts unwiederbringlich verloren.
        per_uid = []   # (uid, msgid, mail_listings)
        per_source: dict[str, int] = {}
        for portal, uids in _alert_uids_by_portal(M).items():
            for uid in uids:
                typ, data = M.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not data or not data[0]:
                    continue
                fetched += 1
                per_source[portal] = per_source.get(portal, 0) + 1
                msg = email.message_from_bytes(data[0][1])
                msgid = (msg.get("Message-ID") or "").strip()
                html = _html_body(msg)
                mail_listings = _parse_html(html, cfg.RESOLVE_TRACKING_LINKS) if html else []
                per_uid.append((uid, msgid, mail_listings))
                listings.extend(mail_listings)

        # mind. eine Mail lieferte Inserate -> Parser/Template sind gesund
        batch_ok = any(ml for _, _, ml in per_uid)

        # 2. Verarbeitete Mails aufräumen: LÖSCHEN, nicht nur als gelesen markieren.
        #    Früher wurden erledigte Mails nur mit \Seen geflaggt und blieben liegen —
        #    das Postfach wuchs unbegrenzt und lief irgendwann in die Provider-Quota
        #    (mailbox.org: 100 MB). Ist die voll, weist der Provider EINGEHENDE Mails
        #    ab → der Bot bekommt gar nichts mehr und der Feed versiegt still.
        #    Deshalb jetzt: fertig verarbeitete Alert-Mails per \Deleted + EXPUNGE
        #    entfernen. Die Inseratsdaten liegen bereits in der DB.
        if not cfg.IMAP_KEEP_UNREAD:
            to_delete = []
            for uid, msgid, ml in per_uid:
                if ml or batch_ok:
                    # geparst, ODER Parser gesund -> diese 0-Mail ist ein echtes
                    # Nicht-Inserat (z.B. Bestätigungsmail): erledigt.
                    done = True
                else:
                    # ganze Charge leer -> evtl. Parser-/Netz-Problem. Mail noch
                    # ein paar Durchläufe behalten (Recovery), dann aufgeben.
                    done = db.bump_email_attempt(cfg.DB_PATH, msgid) >= _MAX_PARSE_ATTEMPTS
                if done:
                    to_delete.append(uid)
            if to_delete:
                # In Batches flaggen (sehr lange UID-Listen sprengen sonst den
                # Command), dann einmal expungen -> gibt den Speicher frei.
                for i in range(0, len(to_delete), 200):
                    batch = b",".join(to_delete[i:i + 200])
                    M.uid("store", batch, "+FLAGS", "(\\Deleted)")
                M.expunge()
        M.close()
    finally:
        M.logout()
    LAST_RUN["fetched"] = fetched
    LAST_RUN["parsed"] = len(listings)
    LAST_RUN["per_source"] = per_source
    return listings


def dump_emails(cfg, out_dir="email_dumps", all_emails=False):
    """Debug: rohe HTML-Bodies der Portal-Mails speichern und geparste
    Listings direkt ausgeben — ohne Link-Auflösung für Geschwindigkeit.
    Markiert nichts als gelesen."""
    import os

    os.makedirs(out_dir, exist_ok=True)
    M = _connect(cfg)
    n = 0
    try:
        for uid in _alert_uids(M, unseen_only=not all_emails):
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

            # Geparste Listings mit Redirect-Auflösung (wie im Live-Betrieb)
            listings = _parse_html(html, resolve_links=True)
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
