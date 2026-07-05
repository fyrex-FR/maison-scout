import html
import json
import re
from math import isfinite
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from app.cities import canonical_city_name, city_slug
from app.crawlers.base import BaseCrawler, CrawledListing


BIEN_ICI_BASE_URL = "https://www.bienici.com"
TARGET_CITIES = ["Frejus", "Saint-Raphael"]

# Bien'ici's place search resolves faster with a postal code suffix for
# known target cities. This is purely a query-building convenience; the city
# name stored on the listing always goes through canonical_city_name.
_KNOWN_POSTAL_CODES = {
    "frejus": "83600",
    "saint-raphael": "83700",
}


def _city_query(city: str) -> str:
    slug = city_slug(city)
    if slug in _KNOWN_POSTAL_CODES:
        return f"{slug}-{_KNOWN_POSTAL_CODES[slug]}"
    return slug


def _first_number(value) -> int | None:
    if isinstance(value, list):
        values = [_first_number(item) for item in value]
        values = [item for item in values if item is not None]
        return min(values) if values else None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"\D+", "", value)
        return int(digits) if digits else None
    return None


def _clean_description(value: str | None) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text = " ".join(html.unescape(text).split())
    return text[:1200] if text else None


def _photos(ad: dict) -> list[str]:
    urls = []
    for photo in ad.get("photos") or []:
        url = photo.get("url") or photo.get("url_photo")
        if url and url not in urls:
            urls.append(url)
    return urls[:8]


def _valid_coords(lat, lon) -> tuple[float | None, float | None]:
    """Validate and coerce a (lat, lon) pair, returning (None, None) if invalid."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (isfinite(lat_f) and isfinite(lon_f)):
        return None, None
    if not (-90.0 <= lat_f <= 90.0):
        return None, None
    if not (-180.0 <= lon_f <= 180.0):
        return None, None
    return lat_f, lon_f


def _coords(ad: dict) -> tuple[float | None, float | None]:
    """Best-effort, defensive extraction of (latitude, longitude) from a
    Bien'ici real-estate-ad payload.

    Bien'ici's public JSON has been observed to expose coordinates under a
    few different shapes depending on the endpoint/version, so we try each
    plausible location in turn and stop at the first one that yields a
    valid pair. Any unexpected structure (missing keys, non-dict values,
    non-numeric coordinates) is swallowed and simply skipped.
    """
    if not isinstance(ad, dict):
        return None, None

    candidates = []

    blur_info = ad.get("blurInfo")
    if isinstance(blur_info, dict):
        position = blur_info.get("position")
        if isinstance(position, dict):
            candidates.append((position.get("lat"), position.get("lon")))

    position = ad.get("position")
    if isinstance(position, dict):
        candidates.append((position.get("lat"), position.get("lon")))
        candidates.append((position.get("latitude"), position.get("longitude")))

    candidates.append((ad.get("latitude"), ad.get("longitude")))
    candidates.append((ad.get("lat"), ad.get("lng")))
    candidates.append((ad.get("lat"), ad.get("lon")))

    for lat, lon in candidates:
        if lat is None or lon is None:
            continue
        lat_f, lon_f = _valid_coords(lat, lon)
        if lat_f is not None and lon_f is not None:
            return lat_f, lon_f

    return None, None


class BienIciCrawler(BaseCrawler):
    source = "bien-ici"

    def __init__(self, cities: list[str] | None = None, page_size: int = 24) -> None:
        self.cities = sorted(set(cities or TARGET_CITIES))
        self.page_size = page_size

    @classmethod
    def from_cities(cls, cities: list[str]) -> "BienIciCrawler":
        return cls(cities=cities or TARGET_CITIES)

    async def crawl(self) -> list[CrawledListing]:
        headers = {
            "User-Agent": "MaisonScout/0.1 (+https://github.com/fyrex-FR/maison-scout)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
            "Referer": f"{BIEN_ICI_BASE_URL}/recherche/achat/france/maisonvilla",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            results: list[CrawledListing] = []
            seen: set[str] = set()
            for city in self.cities:
                city_results = await self._crawl_city(client, city)
                for item in city_results:
                    if item.source_id in seen:
                        continue
                    seen.add(item.source_id)
                    results.append(item)
            return results

    async def _crawl_city(self, client: httpx.AsyncClient, city: str) -> list[CrawledListing]:
        place = await self._load_place(client, city)
        zone_ids = place.get("zoneIds") or []
        if not zone_ids:
            return []

        filters = {
            "filterType": ["buy"],
            "propertyType": ["house"],
            "zoneIdsByTypes": {"zoneIds": zone_ids},
            "size": self.page_size,
            "from": 0,
            "sortBy": "publicationDate",
            "sortOrder": "desc",
            "onTheMarket": [True],
        }
        response = await client.get(
            f"{BIEN_ICI_BASE_URL}/realEstateAds.json",
            params={"filters": json.dumps(filters, separators=(",", ":"))},
        )
        response.raise_for_status()
        data = response.json()
        return [self._parse_ad(ad) for ad in data.get("realEstateAds") or [] if self._is_house_ad(ad)]

    async def _load_place(self, client: httpx.AsyncClient, city: str) -> dict:
        response = await client.get(
            f"{BIEN_ICI_BASE_URL}/place.json",
            params={"q": _city_query(city), "type": "city", "prefix": "no"},
        )
        response.raise_for_status()
        return response.json()

    def _is_house_ad(self, ad: dict) -> bool:
        property_type = str(ad.get("propertyType") or "").lower()
        title = str(ad.get("title") or "").lower()
        description = str(ad.get("description") or "").lower()
        blob = f"{title} {description}"
        if property_type not in {"house", "programme"}:
            return False
        if any(word in blob for word in ("appartement", "studio", "parking", "local commercial")):
            return False
        return True

    def _parse_ad(self, ad: dict) -> CrawledListing:
        source_id = str(ad["id"])
        city = ad.get("city") or "Unknown"
        latitude, longitude = _coords(ad)
        return CrawledListing(
            source=self.source,
            source_id=source_id,
            url=f"{BIEN_ICI_BASE_URL}/annonce/{source_id}",
            title=html.unescape(ad.get("title") or "Annonce Bien'ici"),
            city=canonical_city_name(city),
            postal_code=str(ad.get("postalCode")) if ad.get("postalCode") else None,
            price_eur=_first_number(ad.get("price")),
            living_area_m2=_first_number(ad.get("surfaceArea")),
            land_area_m2=_first_number(ad.get("landSurfaceArea")),
            rooms=_first_number(ad.get("roomsQuantity")),
            bedrooms=_first_number(ad.get("bedroomsQuantity")),
            energy_rating=ad.get("energyClassification"),
            description=_clean_description(ad.get("description")),
            photos=_photos(ad),
            latitude=latitude,
            longitude=longitude,
        )
