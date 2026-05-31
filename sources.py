"""
Inserat-Quellen.

Architektur: jede Quelle ist eine Funktion (search: dict) -> list[Listing].
Neue Quelle = neue Funktion schreiben und in main.SOURCES eintragen.

Flatfox ist die zuverlässigste Quelle, weil sie eine offene, JSON-basierte
Such-API hat und scraping-toleranter ist als Homegate/ImmoScout24
(die hinter Cloudflare/Bot-Schutz sitzen). Falls Felder/Endpoint sich
geaendert haben: kurz die Response loggen und Mapping unten anpassen.
"""
import html
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WohnungsBot/1.0; persoenliche Wohnungssuche)"
}


@dataclass
class Listing:
    id: str
    source: str
    title: str
    price: str
    rooms: str
    space: str
    location: str
    url: str
    image: str | None = None
    available: str | None = None

    def telegram_text(self):
        avail = f"\n📅 {html.escape(self.available)}" if self.available else ""

        # Preis pro m² (wenn beide Felder numerisch parsebar)
        price_per_m2 = ""
        try:
            p_num = float(re.sub(r"[^\d]", "", str(self.price)))
            s_num = float(re.sub(r"[^\d.,]", "", str(self.space)).replace(",", "."))
            if p_num > 100 and s_num > 10:
                price_per_m2 = f"  ·  📊 {p_num / s_num:.0f}/m²"
        except Exception:
            pass

        # Google Maps Link
        maps_url = ""
        if self.location and self.location != "—":
            maps_url = (
                f'\n🗺 <a href="https://maps.google.com/?q='
                f'{quote(self.location + " Schweiz")}">Karte</a>'
            )

        return (
            f"🏠 <b>{html.escape(self.title)}</b>\n\n"
            f"📍 {html.escape(self.location)}\n"
            f"🚪 {html.escape(str(self.rooms))} Zi  ·  "
            f"📐 {html.escape(str(self.space))} m²  ·  "
            f"💰 {html.escape(str(self.price))}"
            f"{price_per_m2}"
            f"{avail}"
            f"{maps_url}\n\n"
            f"🔗 {self.url}\n"
            f"<i>via {self.source} · {html.escape(self.id)}</i>"
        )


def fetch_flatfox(search, cfg=None):
    bbox = search["bbox"]
    params = {
        "offer_type": "RENT",
        "object_category": "APARTMENT",
        "min_price": search["min_price"],
        "max_price": search["max_price"],
        "min_number_of_rooms": search["min_rooms"],
        "max_number_of_rooms": search["max_rooms"],
        "min_living_space": search["min_space"],
        "north": bbox["north"],
        "south": bbox["south"],
        "east": bbox["east"],
        "west": bbox["west"],
        "ordering": "-creation_date",
        "limit": 50,
    }
    r = requests.get(
        "https://flatfox.ch/api/v1/public-flat/",
        params=params,
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()

    out = []
    for f in data.get("results", []):
        pk = str(f.get("pk") or f.get("id") or "")
        if not pk:
            continue

        cover = f.get("cover_image")
        image = cover.get("url") if isinstance(cover, dict) else cover

        rel_url = f.get("url") or f"/de/flat/{pk}/"
        full_url = rel_url if rel_url.startswith("http") else "https://flatfox.ch" + rel_url

        out.append(
            Listing(
                id=f"flatfox-{pk}",
                source="Flatfox",
                title=f.get("public_title") or f.get("title") or "Wohnung",
                price=f.get("price_display") or f.get("price") or "?",
                rooms=f.get("number_of_rooms") or "?",
                space=f.get("living_space") or "?",
                location=f"{f.get('zipcode', '')} {f.get('city', '')}".strip() or "Zürich",
                url=full_url,
                image=image,
            )
        )
    return out


# --- Erweiterungsmuster ----------------------------------------------------
# Homegate / ImmoScout24 haben starken Bot-Schutz. Pragmatisch:
#  - Comparis aggregiert viele Portale -> evtl. dort ansetzen
#  - oder einen Scraping-Dienst mit Cloudflare-Bypass davorschalten
#  - oder WGZimmer/Ronorp ergänzen, falls auch WG infrage kommt
#
# def fetch_xyz(search):
#     r = requests.get(URL, headers=HEADERS, params=..., timeout=20)
#     ...
#     return [Listing(...), ...]
