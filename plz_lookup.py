"""
PLZ-Lookup für Kanton Zürich — alle 162 Gemeinden.

Quelle: GeoNames Switzerland (CC BY 4.0), gefiltert auf Kanton ZH.
Gemeindename → alle zugehörigen PLZ-Codes.

Verwendung:
    from plz_lookup import find_plz, find_gemeinde_name
    find_plz("zürich")      # -> ["8001", "8002", ...]
    find_plz("Winterthur")  # -> ["8400", "8401", ...]
    find_plz("wint")        # Präfix-Suche -> Winterthur
    find_plz("Basel")       # -> [] (nicht Kanton ZH)
"""

_GEMEINDE_PLZ: dict[str, list[str]] = {
    "adlikon": ["8452"],
    "adliswil": ["8134"],
    "aesch (zh)": ["8904"],
    "aeugst am albis": ["8914"],
    "affoltern am albis": ["8909", "8910"],
    "altikon": ["8479"],
    "andelfingen": ["8450"],
    "bachenbülach": ["8184"],
    "bachs": ["8164"],
    "bassersdorf": ["8303"],
    "bauma": ["8493", "8494", "8499"],
    "benken (zh)": ["8463"],
    "berg am irchel": ["8415"],
    "birmensdorf (zh)": ["8903"],
    "bonstetten": ["8906"],
    "boppelsen": ["8113"],
    "brütten": ["8311"],
    "bubikon": ["8608", "8633"],
    "buch am irchel": ["8414"],
    "buchs (zh)": ["8107"],
    "bäretswil": ["8344", "8345"],
    "bülach": ["8180"],
    "dachsen": ["8447"],
    "dielsdorf": ["8157"],
    "dietikon": ["8953"],
    "dietlikon": ["8305"],
    "dinhard": ["8474"],
    "dorf": ["8458"],
    "dägerlen": ["8471"],
    "dällikon": ["8108"],
    "dänikon": ["8114"],
    "dättlikon": ["8421"],
    "dübendorf": ["8044", "8600"],
    "dürnten": ["8632", "8635"],
    "egg": ["8132", "8133"],
    "eglisau": ["8193"],
    "elgg": ["8353", "8354"],
    "ellikon an der thur": ["8548"],
    "elsau": ["8352"],
    "embrach": ["8424"],
    "erlenbach (zh)": ["8703"],
    "fehraltorf": ["8320"],
    "feuerthalen": ["8245", "8246"],
    "fischenthal": ["8496", "8497", "8498"],
    "flaach": ["8416"],
    "flurlingen": ["8247"],
    "freienstein-teufen": ["8427", "8428"],
    "fällanden": ["8117", "8118", "8121"],
    "geroldswil": ["8951", "8954"],
    "glattfelden": ["8192"],
    "gossau (zh)": ["8614", "8624", "8625", "8626"],
    "greifensee": ["8606"],
    "grüningen": ["8627"],
    "hagenbuch": ["8523"],
    "hausen am albis": ["8915", "8925"],
    "hedingen": ["8908"],
    "henggart": ["8444"],
    "herrliberg": ["8704"],
    "hettlingen": ["8442"],
    "hinwil": ["8340", "8342"],
    "hittnau": ["8335"],
    "hochfelden": ["8182"],
    "hombrechtikon": ["8634", "8714"],
    "horgen": ["8135", "8810", "8815", "8816"],
    "humlikon": ["8457"],
    "höri": ["8181"],
    "hüntwangen": ["8194"],
    "hütten": ["8825"],
    "hüttikon": ["8115"],
    "illnau-effretikon": ["8307", "8308", "8314"],
    "kappel am albis": ["8926"],
    "kilchberg (zh)": ["8802"],
    "kleinandelfingen": ["8451", "8453", "8461"],
    "kloten": ["8058", "8060", "8302"],
    "knonau": ["8934"],
    "küsnacht (zh)": ["8127", "8700"],
    "langnau am albis": ["8135"],
    "laufen-uhwiesen": ["8212", "8248"],
    "lindau": ["8310", "8312", "8315", "8317"],
    "lufingen": ["8426"],
    "marthalen": ["8460", "8464"],
    "maschwanden": ["8933"],
    "maur": ["8122", "8123", "8124"],
    "meilen": ["8706"],
    "mettmenstetten": ["8932"],
    "männedorf": ["8708"],
    "mönchaltorf": ["8617"],
    "neerach": ["8173"],
    "neftenbach": ["8412", "8413"],
    "niederglatt": ["8172"],
    "niederhasli": ["8155", "8156"],
    "niederweningen": ["8166"],
    "nürensdorf": ["8309"],
    "oberembrach": ["8425"],
    "oberengstringen": ["8102"],
    "oberglatt": ["8154"],
    "oberrieden": ["8942"],
    "oberweningen": ["8165"],
    "obfelden": ["8912"],
    "oetwil am see": ["8618"],
    "oetwil an der limmat": ["8955"],
    "opfikon": ["8152"],
    "ossingen": ["8475"],
    "otelfingen": ["8112"],
    "ottenbach": ["8913"],
    "pfungen": ["8422"],
    "pfäffikon": ["8330", "8331"],
    "rafz": ["8197"],
    "regensberg": ["8158"],
    "regensdorf": ["8105", "8106"],
    "rheinau": ["8462"],
    "richterswil": ["8805", "8833"],
    "rickenbach (zh)": ["8545"],
    "rifferswil": ["8911"],
    "rorbas": ["8427"],
    "russikon": ["8322", "8332"],
    "rümlang": ["8153"],
    "rüschlikon": ["8803"],
    "rüti (zh)": ["8630"],
    "schlatt (zh)": ["8418"],
    "schleinikon": ["8165"],
    "schlieren": ["8952"],
    "schwerzenbach": ["8603"],
    "schöfflisdorf": ["8165"],
    "seegräben": ["8607"],
    "seuzach": ["8472"],
    "stadel": ["8174", "8175"],
    "stallikon": ["8143"],
    "stammheim": ["8468", "8476", "8477"],
    "steinmaur": ["8162"],
    "stäfa": ["8712", "8713"],
    "thalheim an der thur": ["8478"],
    "thalwil": ["8136", "8800"],
    "truttikon": ["8467"],
    "trüllikon": ["8465", "8466"],
    "turbenthal": ["8488", "8495"],
    "uetikon am see": ["8707"],
    "uitikon": ["8142"],
    "unterengstringen": ["8103"],
    "urdorf": ["8901", "8902"],
    "uster": ["8606", "8610", "8613", "8614", "8615", "8616"],
    "volken": ["8459"],
    "volketswil": ["8604", "8605"],
    "wald (zh)": ["8636", "8637"],
    "wallisellen": ["8304"],
    "wangen-brüttisellen": ["8306", "8602"],
    "wasterkingen": ["8195"],
    "weiach": ["8187"],
    "weiningen (zh)": ["8104"],
    "weisslingen": ["8484"],
    "wettswil am albis": ["8907"],
    "wetzikon (zh)": ["8620", "8623"],
    "wiesendangen": ["8542", "8543", "8544", "8546"],
    "wil (zh)": ["8196"],
    "wila": ["8492"],
    "wildberg": ["8489"],
    "winkel": ["8185"],
    "winterthur": ["8400", "8401", "8403", "8404", "8405", "8406", "8408", "8409"],
    "wädenswil": ["8804", "8820", "8824", "8825"],
    "zell (zh)": ["8483", "8486", "8487"],
    "zollikon": ["8125", "8702"],
    "zumikon": ["8126"],
    "zürich": [
        "8001", "8002", "8003", "8004", "8005", "8006", "8008",
        "8032", "8037", "8038", "8041", "8044", "8045", "8046",
        "8047", "8048", "8049", "8050", "8051", "8052", "8053",
        "8055", "8057", "8064",
    ],
}

# Kurzformen / Aliase (ohne Klammer-Zusatz)
_ALIASES: dict[str, str] = {
    "adliswil":         "adliswil",
    "affoltern":        "affoltern am albis",
    "bauma":            "bauma",
    "birmensdorf":      "birmensdorf (zh)",
    "bubikon":          "bubikon",
    "buchs":            "buchs (zh)",
    "bäretswil":        "bäretswil",
    "benken":           "benken (zh)",
    "dübendorf":        "dübendorf",
    "egg":              "egg",
    "elgg":             "elgg",
    "erlenbach":        "erlenbach (zh)",
    "fällanden":        "fällanden",
    "gossau":           "gossau (zh)",
    "hinwil":           "hinwil",
    "horgen":           "horgen",
    "illnau":           "illnau-effretikon",
    "effretikon":       "illnau-effretikon",
    "kilchberg":        "kilchberg (zh)",
    "kleinandelfingen": "kleinandelfingen",
    "küsnacht":         "küsnacht (zh)",
    "lindau":           "lindau",
    "pfäffikon":        "pfäffikon",
    "regensdorf":       "regensdorf",
    "richterswil":      "richterswil",
    "rickenbach":       "rickenbach (zh)",
    "rüti":             "rüti (zh)",
    "schlatt":          "schlatt (zh)",
    "schlieren":        "schlieren",
    "stadel":           "stadel",
    "uster":            "uster",
    "wald":             "wald (zh)",
    "wädenswil":        "wädenswil",
    "weiningen":        "weiningen (zh)",
    "wetzikon":         "wetzikon (zh)",
    "wil":              "wil (zh)",
    "winterthur":       "winterthur",
    "zell":             "zell (zh)",
    "zürich":           "zürich",
}


def _normalize(s: str) -> str:
    """Normiert auf Kleinbuchstaben, ersetzt Umlaute durch ue/ae/oe."""
    return (
        s.lower()
        .replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")
        .replace("é", "e").replace("è", "e")
        .replace(" (zh)", "").replace(" zh", "")
        .strip()
    )

def _normalize_simple(s: str) -> str:
    """Normiert ohne Umlaut-Ersatz (ü→u) — für Eingaben ohne Umlaute."""
    return (
        s.lower()
        .replace("ü", "u").replace("ä", "a").replace("ö", "o")
        .replace("é", "e").replace("è", "e")
        .replace(" (zh)", "").replace(" zh", "")
        .strip()
    )

# Such-Index aufbauen: normierter Name → Schlüssel in _GEMEINDE_PLZ
_INDEX: dict[str, str] = {}
for _k in _GEMEINDE_PLZ:
    _INDEX[_normalize(_k)] = _k
    _INDEX[_normalize_simple(_k)] = _k
    # Auch ohne Klammer-Zusatz
    _short_full   = _normalize(_k.split("(")[0].strip())
    _short_simple = _normalize_simple(_k.split("(")[0].strip())
    for _s in (_short_full, _short_simple):
        if _s not in _INDEX:
            _INDEX[_s] = _k
# Aliase eintragen
for _alias, _target in _ALIASES.items():
    for _n in (_normalize(_alias), _normalize_simple(_alias)):
        if _n not in _INDEX and _target in _GEMEINDE_PLZ:
            _INDEX[_n] = _target


def find_plz(query: str) -> list[str]:
    """Gibt PLZ-Liste für eine Zürcher Gemeinde zurück, [] wenn nicht gefunden."""
    for norm in (_normalize(query), _normalize_simple(query)):
        if norm in _INDEX:
            return list(_GEMEINDE_PLZ[_INDEX[norm]])
        matches = [k for k in _INDEX if k.startswith(norm) and len(norm) >= 3]
        if len(matches) == 1:
            return list(_GEMEINDE_PLZ[_INDEX[matches[0]]])
    return []


def find_gemeinde_name(query: str) -> str | None:
    """Gibt den offiziellen Gemeindenamen zurück."""
    for norm in (_normalize(query), _normalize_simple(query)):
        if norm in _INDEX:
            return _INDEX[norm].title()
        matches = [k for k in _INDEX if k.startswith(norm) and len(norm) >= 3]
        if len(matches) == 1:
            return _INDEX[matches[0]].title()
    return None
