"""
IMAP-Login — providerneutral.

`imap_login()` meldet eine offene imaplib-Verbindung an:
- Sind OAuth-Werte konfiguriert (Client-ID/Secret/Refresh-Token), per
  SASL-XOAUTH2 (Gmail-tauglich; Refresh-Token einmalig via oauth_setup.py).
- Sonst klassisch mit Benutzer + Passwort (aktueller Weg: mailbox.org).

Hintergrund OAuth: Gmail widerrief App-Passwörter bei Dauer-Logins von einer
Rechenzentrums-IP immer wieder. Seit dem Wechsel auf mailbox.org wird der
klassische Passwort-Login genutzt; der OAuth-Pfad bleibt als Option erhalten.
Der Refresh läuft über `requests` (kein google-Paket auf dem VPS nötig).
"""
import base64
import time

import requests

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# In-Memory-Cache: Access-Token kurz wiederverwenden statt bei jedem Login
# neu zu holen (Token gilt ~1h). Form: {"token": str, "exp": epoch}
_cache: dict = {}


def oauth_enabled(cfg) -> bool:
    """True, wenn ein OAuth-Refresh-Token konfiguriert ist."""
    return bool(
        getattr(cfg, "OAUTH_REFRESH_TOKEN", "")
        and getattr(cfg, "OAUTH_CLIENT_ID", "")
        and getattr(cfg, "OAUTH_CLIENT_SECRET", "")
    )


def get_access_token(cfg) -> str:
    """Frischen Access-Token liefern (aus Cache oder per Refresh).

    Wirft requests.HTTPError, wenn der Refresh-Token ungültig ist — das ist
    dann ein echter Auth-Fehler (z.B. Zugriff entzogen) und soll oben als
    solcher gemeldet werden.
    """
    now = time.time()
    cached = _cache.get("token")
    if cached and _cache.get("exp", 0) - 60 > now:
        return cached

    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": cfg.OAUTH_CLIENT_ID,
            "client_secret": cfg.OAUTH_CLIENT_SECRET,
            "refresh_token": cfg.OAUTH_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _cache["token"] = token
    _cache["exp"] = now + int(data.get("expires_in", 3600))
    return token


def _xoauth2_bytes(user: str, access_token: str) -> bytes:
    """Baut den SASL-XOAUTH2-Initial-Response (un-base64; imaplib kodiert)."""
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return raw.encode("utf-8")


def imap_login(M, cfg) -> None:
    """Meldet die offene imaplib-Verbindung an — per XOAUTH2 falls OAuth
    konfiguriert ist, sonst klassisch mit App-Passwort.

    imaplib base64-kodiert den Rückgabewert des authobject selbst, daher
    geben wir die rohen Bytes zurück.
    """
    if oauth_enabled(cfg):
        token = get_access_token(cfg)
        auth = _xoauth2_bytes(cfg.IMAP_USER, token)
        M.authenticate("XOAUTH2", lambda _: auth)
    else:
        M.login(cfg.IMAP_USER, cfg.IMAP_PASS)
