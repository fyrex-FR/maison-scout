"""Single source of truth for city name normalization.

Both crawlers (Green-Acres, Bien'ici) and the ingest pipeline should route any
city name through :func:`canonical_city_name` before it is stored or compared.
This avoids duplicated hardcoded special-cases scattered across the codebase
(e.g. "Fréjus" vs "Frejus" vs "FREJUS") and keeps deduplication logic reliable.
"""

import re
import unicodedata

# Known aliases / variants -> canonical city name.
# Keys are matched against a "loosely normalized" form of the input (accents
# stripped, lowercased, punctuation collapsed to single spaces) so that
# "St-Raphaël", "Saint-Raphaël", "saint raphael", etc. all resolve to the
# same key.
_CITY_ALIASES: dict[str, str] = {
    "frejus": "Frejus",
    "st raphael": "Saint-Raphael",
    "saint raphael": "Saint-Raphael",
    "st raphaël": "Saint-Raphael",
    "saint raphaël": "Saint-Raphael",
    "aulnay sous bois": "Aulnay Sous Bois",
}

# Single source of truth for per-city metadata needed to build search URLs /
# query params for external sources (protected sources scraped by OpenClaw,
# and in-repo crawlers like ParuVendu that require a postal code in their
# search URL slug). Keyed by canonical city name (see canonical_city_name).
CITY_METADATA: dict[str, dict[str, str]] = {
    "Frejus": {"postal_code": "83600", "seloger_department": "83"},
    "Saint-Raphael": {"postal_code": "83700", "seloger_department": "83"},
    "Cannes": {"postal_code": "06400", "seloger_department": "06"},
    "Mougins": {"postal_code": "06250", "seloger_department": "06"},
    "Mandelieu-La-Napoule": {"postal_code": "06210", "seloger_department": "06"},
    "Theoule-Sur-Mer": {"postal_code": "06590", "seloger_department": "06"},
    "Sainte-Maxime": {"postal_code": "83120", "seloger_department": "83"},
    "Saint-Tropez": {"postal_code": "83990", "seloger_department": "83"},
    "Roquebrune-Sur-Argens": {"postal_code": "83520", "seloger_department": "83"},
    "Puget-Sur-Argens": {"postal_code": "83480", "seloger_department": "83"},
}


def _strip_accents(value: str) -> str:
    """Remove diacritics, e.g. 'é' -> 'e'."""
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _loose_key(value: str) -> str:
    """Loose lookup key: no accents, lowercase, punctuation -> single spaces."""
    ascii_value = _strip_accents(value).lower()
    ascii_value = re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()
    return ascii_value


def _default_capitalize(value: str) -> str:
    """Deterministic fallback capitalization for an unknown city.

    Produces a clean, accent-free, hyphen-normalized title case, e.g.
    "  le  cannet-des-maures " -> "Le-Cannet-Des-Maures" is avoided in favor of
    keeping spaces as spaces and hyphens as hyphens, each word capitalized:
    "saint tropez" -> "Saint Tropez", "sainte-maxime" -> "Sainte-Maxime".
    """
    ascii_value = _strip_accents(value).strip()
    # Collapse repeated whitespace, keep existing hyphens/spaces as word
    # separators, drop any other punctuation noise.
    ascii_value = re.sub(r"[^a-zA-Z0-9\s-]+", "", ascii_value)
    ascii_value = re.sub(r"\s+", " ", ascii_value).strip()

    def _capitalize_word(word: str) -> str:
        return word[:1].upper() + word[1:].lower() if word else word

    parts = []
    for space_part in ascii_value.split(" "):
        if not space_part:
            continue
        hyphen_parts = [_capitalize_word(p) for p in space_part.split("-")]
        parts.append("-".join(hyphen_parts))
    return " ".join(parts) if parts else ""


def canonical_city_name(name: str) -> str:
    """Return the canonical form of a city name.

    - Known aliases/variants (accents, casing, "St-" vs "Saint-", spacing)
      resolve to a single canonical spelling, e.g. "Fréjus" -> "Frejus" and
      "St-Raphaël" / "Saint-Raphaël" / "saint raphael" -> "Saint-Raphael".
    - Unknown cities never raise: they are normalized deterministically
      (accents removed, whitespace/hyphen cleaned up, each word capitalized)
      so that the same input always yields the same output and can be safely
      used for grouping/deduplication/URLs.
    """
    if not name or not name.strip():
        return ""

    key = _loose_key(name)
    if key in _CITY_ALIASES:
        return _CITY_ALIASES[key]

    return _default_capitalize(name)


def city_slug(name: str) -> str:
    """Return a lowercase, URL-friendly slug for a city name.

    Uses the canonical name so aliases collapse to the same slug, e.g.
    both "Fréjus" and "FREJUS" -> "frejus".
    """
    canonical = canonical_city_name(name)
    slug = _strip_accents(canonical).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug
