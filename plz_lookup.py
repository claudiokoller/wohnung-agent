"""
PLZ-Lookup für Kanton Zürich.

Quelle: Schweizer Post / BFS, eingebettet als statische Tabelle.
Nur Kanton Zürich — für die Wohnungssuche der Bot-Nutzer.

Verwendung:
    from plz_lookup import find_plz
    plz_list = find_plz("zürich")   # -> ["8001", "8002", ...]
    plz_list = find_plz("Opfikon")  # -> ["8152"]
    plz_list = find_plz("xyz")      # -> []
"""

# Gemeinde → PLZ-Liste (Kanton Zürich)
# Schlüssel sind lowercase ohne Umlaute-Normierung — Suche erfolgt über find_plz().
_GEMEINDE_PLZ: dict[str, list[str]] = {
    # --- Stadt Zürich (nach Stadtkreisen) ---
    "zürich": [
        "8001", "8002", "8003", "8004", "8005", "8006", "8008",
        "8032", "8037", "8038", "8041", "8044", "8045", "8046",
        "8047", "8048", "8049", "8050", "8051", "8052", "8053",
        "8055", "8057", "8064",
    ],
    # --- Winterthur & Umgebung ---
    "winterthur":           ["8400", "8404", "8405", "8406", "8408"],
    "seuzach":              ["8413"],
    "pfungen":              ["8422"],
    "embrach":              ["8424"],
    "elsau":                ["8352"],
    "wiesendangen":         ["8542"],
    "bertschikon":          ["8532"],
    # --- Glattal / Flughafen-Region ---
    "opfikon":              ["8152"],
    "kloten":               ["8302"],
    "bassersdorf":          ["8303"],
    "wallisellen":          ["8304"],
    "wangen-brüttisellen":  ["8306"],
    "rümlang":              ["8153"],
    "nürensdorf":           ["8309"],
    "bachenbülach":         ["8184"],
    "hochfelden":           ["8182"],
    "bülach":               ["8180"],
    "glattfelden":          ["8192"],
    "eglisau":              ["8193"],
    # --- Limmattal ---
    "schlieren":            ["8952"],
    "dietikon":             ["8953"],
    "urdorf":               ["8902"],
    "birmensdorf":          ["8903"],
    "geroldswil":           ["8954"],
    "oetwil an der limmat": ["8955"],
    "oberengstringen":      ["8102"],
    "unterengstringen":     ["8103"],
    "weiningen":            ["8104"],
    "otelfingen":           ["8112"],
    "buchs":                ["8107"],
    "regensdorf":           ["8105", "8106"],
    # --- Furttal ---
    "dällikon":             ["8108"],
    "dänikon":              ["8114"],
    "steinmaur":            ["8162"],
    "niederglatt":          ["8172"],
    "niederhasli":          ["8155"],
    # --- Zürich Süd / Sihltal ---
    "adliswil":             ["8134"],
    "kilchberg":            ["8802"],
    "rüschlikon":           ["8803"],
    "thalwil":              ["8800"],
    "langnau am albis":     ["8135"],
    "stallikon":            ["8143"],
    "uitikon":              ["8142"],
    # --- Zürichsee links ---
    "horgen":               ["8810"],
    "wädenswil":            ["8820"],
    "richterswil":          ["8805"],
    "schönenberg":          ["8824"],
    "hütten":               ["8825"],
    # --- Zürichsee rechts ---
    "küsnacht":             ["8700"],
    "erlenbach":            ["8703"],
    "herrliberg":           ["8704"],
    "meilen":               ["8706"],
    "uetikon am see":       ["8707"],
    "männedorf":            ["8708"],
    "stäfa":                ["8712"],
    "zollikon":             ["8702"],
    "zumikon":              ["8126"],
    # --- Pfannenstiel ---
    "maur":                 ["8124"],
    "greifensee":           ["8606"],
    "volketswil":           ["8605"],
    # --- Usteramt ---
    "uster":                ["8610"],
    "dübendorf":            ["8600"],
    "schwerzenbach":        ["8603"],
    "fehraltorf":           ["8320"],
    "lindau":               ["8315"],
    "russikon":             ["8332"],
    "pfäffikon":            ["8330"],
    "hittnau":              ["8335"],
    "weisslingen":          ["8484"],
    # --- Hinwil ---
    "hinwil":               ["8340"],
    "bäretswil":            ["8344"],
    "bubikon":              ["8608"],
    "wolfhausen":           ["8633"],
    "grüningen":            ["8627"],
    "gossau":               ["8625"],
    "dürnten":              ["8635"],
    "rüti":                 ["8630"],
    "wetzikon":             ["8620", "8623"],
    "hombrechtikon":        ["8634"],
    "wald":                 ["8636"],
    # --- Illnau-Effretikon / Freiamt ---
    "illnau-effretikon":    ["8307"],
    # --- Andelfingen ---
    "andelfingen":          ["8450"],
    "marthalen":            ["8460"],
    "flaach":               ["8416"],
    # --- Tösstal / Zürcher Bergland ---
    "turbenthal":           ["8488"],
    "wila":                 ["8492"],
    "zell":                 ["8487"],
    "bauma":                ["8494"],
    "fischenthal":          ["8497"],
    "wildberg":             ["8489"],
}

# Umlaute normieren für robuste Suche
def _normalize(s: str) -> str:
    return (
        s.lower()
        .replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")
        .replace("é", "e").replace("è", "e").strip()
    )

# Normierter Index: normierter Name → Original-Schlüssel
_INDEX: dict[str, str] = {_normalize(k): k for k in _GEMEINDE_PLZ}


def find_plz(query: str) -> list[str]:
    """Sucht PLZ-Liste für eine Zürcher Gemeinde.
    Gibt leere Liste zurück wenn nicht gefunden."""
    key = _normalize(query)
    # Exakter Treffer
    if key in _INDEX:
        return list(_GEMEINDE_PLZ[_INDEX[key]])
    # Präfix-Suche (z.B. "winti" findet "winterthur")
    matches = [k for k in _INDEX if k.startswith(key)]
    if len(matches) == 1:
        return list(_GEMEINDE_PLZ[_INDEX[matches[0]]])
    return []


def find_gemeinde_name(query: str) -> str | None:
    """Gibt den offiziellen Gemeindenamen zurück (für Anzeige)."""
    key = _normalize(query)
    if key in _INDEX:
        return _INDEX[key].title()
    matches = [k for k in _INDEX if k.startswith(key)]
    if len(matches) == 1:
        return _INDEX[matches[0]].title()
    return None
