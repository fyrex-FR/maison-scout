import html
import json
import re
import unicodedata
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from app.crawlers.base import BaseCrawler, CrawledListing


BIEN_ICI_BASE_URL = "https://www.bienici.com"
TARGET_CITIES = ["Frejus", "Saint-Raphael"]


def _city_query(city: str) -> str:
    normalized = unicodedata.normalize("NFKD", city.strip()).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    postal_codes = {
        "frejus": "83600",
        "saint-raphael": "83700",
        "saint-raphael-83700": "83700",
    }
    if normalized in postal_codes:
        return f"{normalized}-{postal_codes[normalized]}"
    return normalized


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
        return CrawledListing(
            source=self.source,
            source_id=source_id,
            url=f"{BIEN_ICI_BASE_URL}/annonce/{source_id}",
            title=html.unescape(ad.get("title") or "Annonce Bien'ici"),
            city=city.replace("é", "e") if city == "Fréjus" else city,
            postal_code=str(ad.get("postalCode")) if ad.get("postalCode") else None,
            price_eur=_first_number(ad.get("price")),
            living_area_m2=_first_number(ad.get("surfaceArea")),
            land_area_m2=_first_number(ad.get("landSurfaceArea")),
            rooms=_first_number(ad.get("roomsQuantity")),
            bedrooms=_first_number(ad.get("bedroomsQuantity")),
            energy_rating=ad.get("energyClassification"),
            description=_clean_description(ad.get("description")),
            photos=_photos(ad),
        )
