import base64
import html
import json
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.crawlers.base import BaseCrawler, CrawledListing


GREEN_ACRES_BASE_URL = "https://www.green-acres.fr"
TARGET_URLS = [
    "https://www.green-acres.fr/immobilier/frejus",
    "https://www.green-acres.fr/immobilier/saint-raphael",
]
HOUSE_WORDS = ("maison", "villa", "propriete", "propriété", "demeure")
APARTMENT_WORDS = ("appartement", "studio", "t2", "t3", "t4")
EXCLUDED_PATH_PARTS = ("/appartement/", "/terrain/", "/local-commercial/", "/parking/")


def _number(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"\D+", "", html.unescape(value))
    return int(digits) if digits else None


def _decode_url(encoded: str | None) -> str | None:
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return None


def _text(node) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _city_and_postal(raw: str) -> tuple[str, str | None]:
    value = html.unescape(raw)
    if "Saint" in value and "Rapha" in value:
        return "Saint-Raphael", "83700"
    if "Frejus" in value or "Fréjus" in value:
        return "Frejus", "83600"
    city = value.split("(")[0].strip() or "Unknown"
    return city, None


def _extract_photos(card) -> list[str]:
    photos: list[str] = []
    for img in card.select("img.announce-card-img"):
        url = img.get("src") or img.get("data-lazy-src")
        if url:
            photos.append(url)

    carousel = card.select_one(".f-carousel")
    if carousel and carousel.get("data-remaining-slides"):
        try:
            photos.extend(json.loads(html.unescape(carousel["data-remaining-slides"])))
        except json.JSONDecodeError:
            pass

    deduped = []
    seen = set()
    for url in photos:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped[:8]


def _extract_characteristics(card) -> tuple[int | None, int | None, int | None]:
    living_area = None
    land_area = None
    rooms = None

    for tag in card.select(".characteristics .info-tag"):
        title = html.unescape(tag.get("title", "")).lower()
        value = _text(tag)
        if "surface habitable" in title:
            living_area = _number(value)
        elif "terrain" in title:
            land_area = _number(value)
        elif "pi" in title:
            rooms = _number(value)

    return living_area, land_area, rooms


def _extract_bedrooms(description: str) -> int | None:
    patterns = (
        r"(\d+)\s+chambres?",
        r"(\d+)\s+suite",
    )
    for pattern in patterns:
        match = re.search(pattern, description.lower())
        if match:
            return int(match.group(1))
    return None


class GreenAcresCrawler(BaseCrawler):
    source = "green-acres"

    def __init__(self, urls: list[str] | None = None) -> None:
        self.urls = urls or TARGET_URLS

    async def crawl(self) -> list[CrawledListing]:
        headers = {
            "User-Agent": "MaisonScout/0.1 (+https://github.com/fyrex-FR/maison-scout)",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            results: list[CrawledListing] = []
            for url in self.urls:
                response = await client.get(url)
                response.raise_for_status()
                results.extend(self._parse(response.text))
            return results

    def _parse(self, content: str) -> list[CrawledListing]:
        soup = BeautifulSoup(content, "html.parser")
        listings: list[CrawledListing] = []

        for card in soup.select(".announce-card[data-advertid]"):
            title = html.unescape(card.get("title", "")).strip()
            normalized_title = title.lower()
            description = _text(card.select_one(".description-details"))
            normalized_blob = f"{normalized_title} {description.lower()}"

            if not any(word in normalized_blob for word in HOUSE_WORDS):
                continue
            if any(word in normalized_title for word in APARTMENT_WORDS):
                continue

            source_id = card.get("data-advertid")
            if not source_id:
                continue

            decoded_url = _decode_url(card.get("data-o"))
            listing_url = urljoin(GREEN_ACRES_BASE_URL, decoded_url or "")
            if any(path_part in listing_url for path_part in EXCLUDED_PATH_PARTS):
                continue

            city, postal_code = _city_and_postal(_text(card.select_one(".announce-localisation")))
            living_area, land_area, rooms = _extract_characteristics(card)

            listings.append(
                CrawledListing(
                    source=self.source,
                    source_id=source_id,
                    url=listing_url,
                    title=title,
                    city=city,
                    postal_code=postal_code,
                    price_eur=_number(_text(card.select_one(".info-price"))),
                    living_area_m2=living_area,
                    land_area_m2=land_area,
                    rooms=rooms,
                    bedrooms=_extract_bedrooms(description),
                    energy_rating=None,
                    description=description[:1200] if description else None,
                    photos=_extract_photos(card),
                )
            )

        return listings
