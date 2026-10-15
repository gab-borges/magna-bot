"""Find a country from what someone typed: English or Portuguese, codes, or
other common spellings, ignoring accents, case and small typos.

countries.json comes from https://github.com/mledoze/countries (ODbL 1.0),
kept to the names (English, Portuguese and alternative spellings), codes and
the coordinates of each country's center. The Portuguese names shown ("pt")
are Brazilian; the European ones they replaced are still accepted.
"""
import difflib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

DATA_FILE = os.path.join(os.path.dirname(__file__), "countries.json")

# Names people use that the data doesn't have (or gives another country).
EXTRA_NAMES = {
    "ARE": ["UAE", "EAU", "Emirados Arabes", "Emirados"],
    "CZE": ["Czech Republic", "Republica Tcheca", "Tchequia", "Tchecoslovaquia", "Czechoslovakia"],
    "DEU": ["Alemanha Oriental", "Alemanha Ocidental", "East Germany", "West Germany"],
    "GBR": [
        "Inglaterra", "England", "Escocia", "Scotland", "Pais de Gales", "Wales",
        "Irlanda do Norte", "Northern Ireland", "Gra Bretanha", "Britain",
    ],
    "KOR": ["Coreia"],
    "MMR": ["Birmania"],
    "NLD": ["Paises Baixos"],
    "PSE": ["Palestinian territories", "Territorios Palestinos"],
    "RUS": ["URSS", "USSR", "Uniao Sovietica", "Soviet Union"],
    "SRB": ["Iugoslavia", "Yugoslavia"],
    "TUR": ["Turkey"],
    "TWN": ["Formosa"],
    "USA": ["EUA", "America", "Estados Unidos da America"],
}

# Names a fuzzy match is tried for; shorter ones (codes) only match exactly.
FUZZY_MIN_LENGTH = 4
FUZZY_CUTOFF = 0.85


@dataclass(frozen=True)
class Country:
    code: str  # ISO 3166-1 alpha-3
    name: str  # in Portuguese
    lat: float
    lng: float


def normalize(text):
    """Lowercase, without accents or punctuation: "Côte d'Ivoire" -> "cote d ivoire"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


@lru_cache(maxsize=None)
def index():
    """normalized name -> Country.

    Common names are indexed first, so they win over another country's
    official name or alternative spelling that happens to be the same.
    """
    with open(DATA_FILE, encoding="utf-8") as f:
        entries = json.load(f)
    countries = {
        entry["code"]: Country(entry["code"], entry["pt"], *entry["latlng"])
        for entry in entries
    }
    names = {}
    passes = [
        [(entry["code"], entry["names"][0]) for entry in entries],
        [(entry["code"], entry["pt"]) for entry in entries],
        [(code, name) for code, extra in EXTRA_NAMES.items() for name in extra],
        [(entry["code"], name) for entry in entries for name in entry["names"]],
    ]
    for names_pass in passes:
        for code, name in names_pass:
            key = normalize(name)
            if key:
                names.setdefault(key, countries[code])
    return names


@lru_cache(maxsize=1024)
def find(text):
    """The Country `text` names, or None.

    An exact name comes first (so "Niger" isn't taken for "Nigeria"), then
    the one country whose name starts with it ("Bosnia"), then the closest
    name, to forgive a typo ("Alemanah").
    """
    key = normalize(text)
    names = index()
    if key in names:
        return names[key]
    if len(key) < FUZZY_MIN_LENGTH:
        return None
    starting = {country for name, country in names.items() if name.startswith(key + " ")}
    if len(starting) == 1:
        return starting.pop()
    close = difflib.get_close_matches(key, names, n=1, cutoff=FUZZY_CUTOFF)
    return names[close[0]] if close else None


def find_all(text):
    """The countries an answer names; some are "X or Y"."""
    parts = re.split(r"\s+(?:or|ou)\s+|/", text)
    return [country for country in map(find, parts) if country]
