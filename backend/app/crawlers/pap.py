"""Crawler for PAP.fr (De Particulier a Particulier).

NOTE ON HTML STRUCTURE ASSUMPTION
----------------------------------
pap.fr sits behind a Cloudflare "managed challenge" (JS/browser check) that
returns HTTP 403 with a "Just a moment..." interstitial to plain httpx
requests -- this was confirmed while building this crawler (both a direct
httpx-style fetch and a browser-UA curl request were challenged). It was
therefore not possible to inspect a live, real search-results page to model
selectors with certainty.

This parser is a best-effort implementation based on PAP's well-known,
long-standing public markup conventions for search result listing cards
(as documented in prior integrations / publicly visible page source before
Cloudflare was added):

- Each result is an `<li class="item">` (or `<div class="item">` for some
  layouts) inside `.search-list` / `#site-list-results`, wrapping a single
  `<a class="item-link" href=".../annonce/vente-maison-ville-cp-idXXXXXXX.html">`.
- The listing id is embedded at the end of the URL slug as `idXXXXXXX` (or
  exposed via a `data-id` / `data-idannonce` attribute on the item), e.g.
  ".../vente-maison-frejus-83600-r1234567.html" -> id "1234567".
- Title lives in `.item-title` (e.g. "Maison 5 pieces 120 m2").
- Price lives in `.item-price` as "450 000 €" (space-separated thousands,
  euro sign, sometimes preceded/followed by "Prix" or a per-m2 breakdown in a
  child node that must not be picked up).
- Tags (rooms/bedrooms/surface/land) live in a `.item-tags li` list, e.g.
  "120 m2", "5 pieces", "3 chambres", "terrain 650 m2".
- Location lives in `.item-city` as "Frejus (83600)".
- Description lives in `.item-description`.
- Photos live in `.item-photos img` (`src` or `data-src` for lazy-loaded
  images).
- Property type (maison/appartement/terrain/...) is generally embedded in
  the title and/or the URL slug ("vente-maison-...", "vente-appartement-...").

If/when a real page becomes reachable (e.g. via an authorized proxy or a
manually saved page), the selectors below should be revisited and the
fixture (`tests/fixtures/pap_frejus_search.html`) replaced with real markup;
this module's `_parse` is written defensively (best-effort, returns None on
missing fields) specifically so that such a follow-up adjustment is cheap.
"""

import html
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.cities import canonical_city_name, city_slug
from app.crawlers.base import BaseCrawler, CrawledListing


PAP_BASE_URL = "https://www.pap.fr"
TARGET_CITIES = ["Frejus", "Saint-Raphael"]

# Known postal codes for the target cities, used to backfill the postal code
# when PAP's location text doesn't include one. Purely a display
# convenience; the city name itself always goes through canonical_city_name.
_KNOWN_POSTAL_CODES = {
    "frejus": "83600",
    "saint-raphael": "83700",
}

HOUSE_WORDS = ("maison", "villa", "propriete", "propriété", "demeure", "longere", "longère")
# These are checked against the title only (not the free-text description,
# which may legitimately mention e.g. "garage" or "terrain" as an amenity of
# a house) to avoid false-positive exclusions.
EXCLUDED_TITLE_WORDS = (
    "appartement",
    "studio",
    "duplex",
    "terrain",
    "parking",
    "local commercial",
    "local professionnel",
    "immeuble de rapport",
    "chambre de bonne",
)
EXCLUDED_PATH_PARTS = (
    "/annonce/vente-appartement-",
    "/annonce/vente-terrain-",
    "/annonce/vente-parking-",
    "/annonce/vente-garage-",
    "/annonce/vente-local-",
    "/annonce/vente-immeuble-",
)

_ID_IN_URL = re.compile(r"-[a-z]?(\d{5,})(?:\.html)?/?$")


def _number(value: str | None) -> int | None:
    """Extract the leading integer from a free-text value.

    Used for prices ("450 000 €" -> 450000) and counts ("5 pieces" -> 5).
    Deliberately reads the leading run of digits (ignoring thousands
    separators/spaces) rather than stripping all non-digits from the whole
    string, since trailing unit suffixes like "m2"/"m²" contain a literal
    digit that would otherwise corrupt the result (e.g. "120 m2" must not
    become 1202). Use :func:`_area_number` for surface values instead.
    """
    if not value:
        return None
    match = re.match(r"\s*((?:\d[\d\s .]*))", html.unescape(value))
    if not match:
        return None
    digits = re.sub(r"\D+", "", match.group(1))
    return int(digits) if digits else None


def _area_number(value: str | None) -> int | None:
    """Extract a surface figure such as "120 m2" / "650 m²" -> 120 / 650."""
    if not value:
        return None
    match = re.search(r"(\d[\d\s .]*)\s*m[²2]", html.unescape(value), re.IGNORECASE)
    if match:
        digits = re.sub(r"\D+", "", match.group(1))
        return int(digits) if digits else None
    return _number(value)


def _text(node) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _source_id(url: str, item) -> str | None:
    """Best-effort stable id extraction: prefer explicit data attributes,
    fall back to the trailing numeric segment of the listing URL slug.
    """
    for attr in ("data-idannonce", "data-id-annonce", "data-id"):
        value = item.get(attr) if item else None
        if value:
            return str(value)

    match = _ID_IN_URL.search(url or "")
    if match:
        return match.group(1)
    return None


def _city_and_postal(raw: str) -> tuple[str, str | None]:
    """Extract (canonical city name, postal code) from a location blob.

    PAP's location text is typically "City (12345)" or "City 12345". The
    postal code is read when present, otherwise falls back to a small
    known-cities table (display convenience only; does not affect
    matching/deduplication).
    """
    value = html.unescape(raw)
    postal_match = re.search(r"\(?(\d{5})\)?", value)
    raw_city = re.sub(r"\(?\d{5}\)?", "", value).strip() or "Unknown"
    city = canonical_city_name(raw_city)

    postal_code = postal_match.group(1) if postal_match else _KNOWN_POSTAL_CODES.get(city_slug(city))
    return city, postal_code


def _extract_tags(item) -> tuple[int | None, int | None, int | None, int | None]:
    """Return (living_area_m2, land_area_m2, rooms, bedrooms) from the
    `.item-tags` list, e.g. ["120 m2", "5 pieces", "3 chambres",
    "terrain 650 m2"].
    """
    living_area = None
    land_area = None
    rooms = None
    bedrooms = None

    tags = item.select(".item-tags li") if item else []
    for tag in tags:
        text = _text(tag).lower()
        if "terrain" in text:
            land_area = _area_number(text)
        elif "m2" in text or "m²" in text:
            living_area = _area_number(text)
        elif "chambre" in text:
            bedrooms = _number(text)
        elif "pièce" in text or "piece" in text:
            rooms = _number(text)

    return living_area, land_area, rooms, bedrooms


def _extract_bedrooms_from_text(*blobs: str) -> int | None:
    for blob in blobs:
        match = re.search(r"(\d+)\s+chambres?", blob.lower())
        if match:
            return int(match.group(1))
    return None


def _extract_photos(item) -> list[str]:
    photos: list[str] = []
    if not item:
        return photos
    for img in item.select(".item-photos img, .item-photo img, img"):
        url = img.get("data-src") or img.get("src")
        if url and url not in photos:
            photos.append(urljoin(PAP_BASE_URL, url))
    return photos[:8]


class PapCrawler(BaseCrawler):
    source = "pap"

    def __init__(self, cities: list[str] | None = None) -> None:
        self.cities = sorted(set(cities or TARGET_CITIES))

    @classmethod
    def from_cities(cls, cities: list[str]) -> "PapCrawler":
        return cls(cities=cities or TARGET_CITIES)

    def _search_url(self, city: str) -> str:
        slug = city_slug(city)
        # PAP search-by-city URL pattern for house sales, e.g.:
        # https://www.pap.fr/annonce/vente-maison-frejus-83600
        postal_code = _KNOWN_POSTAL_CODES.get(slug)
        if postal_code:
            return f"{PAP_BASE_URL}/annonce/vente-maison-{slug}-{postal_code}"
        return f"{PAP_BASE_URL}/annonce/vente-maison-{slug}"

    async def crawl(self) -> list[CrawledListing]:
        headers = {
            "User-Agent": "MaisonScout/0.1 (+https://github.com/fyrex-FR/maison-scout)",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            results: list[CrawledListing] = []
            seen: set[str] = set()
            for city in self.cities:
                url = self._search_url(city)
                response = await client.get(url)
                response.raise_for_status()
                for listing in self._parse(response.text):
                    if listing.source_id in seen:
                        continue
                    seen.add(listing.source_id)
                    results.append(listing)
            return results

    def _parse(self, content: str) -> list[CrawledListing]:
        soup = BeautifulSoup(content, "html.parser")
        listings: list[CrawledListing] = []
        seen: set[str] = set()

        for item in soup.select(".search-list .item, #site-list-results .item"):
            link = item.select_one("a.item-link, a.item-title-link, a")
            href = link.get("href") if link else None
            listing_url = urljoin(PAP_BASE_URL, href) if href else None

            title_node = item.select_one(".item-title")
            title = _text(title_node) or (html.unescape(link.get("title", "")).strip() if link else "")
            description = _text(item.select_one(".item-description"))
            normalized_title = title.lower()
            blob = f"{normalized_title} {description.lower()} {(href or '').lower()}"

            if not any(word in blob for word in HOUSE_WORDS):
                continue
            if any(word in normalized_title for word in EXCLUDED_TITLE_WORDS):
                continue
            if listing_url and any(part in listing_url.lower() for part in EXCLUDED_PATH_PARTS):
                continue

            source_id = _source_id(listing_url or href or "", item)
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)

            city, postal_code = _city_and_postal(_text(item.select_one(".item-city")))
            living_area, land_area, rooms, bedrooms = _extract_tags(item)
            if bedrooms is None:
                bedrooms = _extract_bedrooms_from_text(description, title)

            price_node = item.select_one(".item-price")
            price_eur = _number(_text(price_node)) if price_node else None

            listings.append(
                CrawledListing(
                    source=self.source,
                    source_id=source_id,
                    url=listing_url or "",
                    title=title or "Annonce PAP",
                    city=city,
                    postal_code=postal_code,
                    price_eur=price_eur,
                    living_area_m2=living_area,
                    land_area_m2=land_area,
                    rooms=rooms,
                    bedrooms=bedrooms,
                    energy_rating=None,
                    description=description[:1200] if description else None,
                    photos=_extract_photos(item),
                )
            )

        return listings
