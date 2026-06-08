"""
Einmaliges OAuth2-Setup für den Gmail-Zugang des Bots.

Holt über den Google-Login im Browser einen langlebigen REFRESH-TOKEN und
gibt die drei Werte aus, die in die .env auf dem VPS gehören. Danach läuft
der Bot dauerhaft ohne App-Passwort.

Voraussetzung (einmalig in der Google Cloud Console):
  1. Projekt anlegen, Gmail API aktivieren.
  2. OAuth-Consent-Screen: "Extern", Test-User = die Bot-Mailadresse.
  3. Anmeldedaten -> OAuth-Client-ID -> Typ "Desktop-App".
  4. JSON herunterladen, hier als client_secret.json daneben legen.

Dann lokal (NICHT auf dem VPS — braucht einen Browser):
  pip install google-auth-oauthlib
  python oauth_setup.py

Am Ende werden WOHNUNGS_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN ausgegeben.
"""
import json
import os
import sys

CLIENT_FILE = "client_secret.json"

# Nur Lesen + Drafts anlegen — kein Vollzugriff.
SCOPES = ["https://mail.google.com/"]


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit(
            "Fehlt: google-auth-oauthlib.\n"
            "  pip install google-auth-oauthlib\n"
            "dann erneut: python oauth_setup.py"
        )

    if not os.path.exists(CLIENT_FILE):
        sys.exit(
            f"'{CLIENT_FILE}' nicht gefunden.\n"
            "Lade die OAuth-Client-JSON (Desktop-App) aus der Google Cloud "
            f"Console herunter und speichere sie hier als '{CLIENT_FILE}'."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
    # access_type=offline + prompt=consent erzwingt einen Refresh-Token.
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="consent"
    )

    if not creds.refresh_token:
        sys.exit(
            "Kein Refresh-Token erhalten. In den Google-Konto-Einstellungen "
            "unter 'Drittanbieter-Apps' den bestehenden Zugang entfernen und "
            "erneut ausführen."
        )

    with open(CLIENT_FILE) as f:
        info = json.load(f)
    data = info.get("installed") or info.get("web") or {}

    print("\n" + "=" * 60)
    print("FERTIG. Diese drei Zeilen in die .env auf dem VPS eintragen:")
    print("=" * 60)
    print(f"WOHNUNGS_OAUTH_CLIENT_ID={data.get('client_id', '')}")
    print(f"WOHNUNGS_OAUTH_CLIENT_SECRET={data.get('client_secret', '')}")
    print(f"WOHNUNGS_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    print("Danach: systemctl restart wohnungs-bot wohnungs-bot-cmd")


if __name__ == "__main__":
    main()
