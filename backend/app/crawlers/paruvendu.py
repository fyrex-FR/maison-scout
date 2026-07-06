"""Crawler for ParuVendu.fr (houses/villas for sale).

WHY THIS EXISTS
----------------
Unlike PAP.fr (Cloudflare-challenged, see crawlers/pap.py) or SeLoger
(DataDome), ParuVendu.fr was confirmed reachable with a plain httpx request
carrying a realistic browser User-Agent and no anti-bot protection kicked in
(HTTP 200, server-rendered HTML). This makes it viable to run directly from
the backend, like Green-Acres and Bien'ici, instead of routing it through the
external OpenClaw browser worker (see docs/PROJECT_CONTEXT.md section 3 for
the crawler/backend/OpenClaw split).

URL PATTERN AND CITY METADATA
------------------------------
ParuVendu's search URL requires both a city slug and its postal code, e.g.:
    https://www.paruvendu.fr/immobilier/vente/maison/frejus-83600/
The postal code isn't derivable from the city name alone, so this crawler
reuses `app.cities.CITY_METADATA` (the single source of truth for per-city
postal codes, also used by the `/api/ingest/protected-source-targets`
endpoint). A followed city with no entry in CITY_METADATA is silently
skipped -- we don't know its postal code, so we can't build a valid search
URL, and this must never raise (a new followed city without metadata is a
normal, expected state, not an error).

HTML STRUCTURE (validated against a real saved search-results page)
---------------------------------------------------------------------
- Each listing card is a `<div class="blocAnnonce" data-id="...">`. The
  numeric `data-id` is a coarser id shared by the ld+json/UI; the actual
  listing id used across the app is the trailing slug segment of the detail
  URL (e.g. "1291835860A1KIVHMN000"), which is what we store as source_id.
- The detail link lives in `h3 a[href]` (href like
  "/immobilier/vente/maison/<id>" or ".../vente/villa/<id>"). Its `title`
  attribute holds a clean summary, e.g. " Maison - 2 pièce(s) - 36 m²",
  used as the listing title; its text content is a fallback (e.g.
  "Maison\\n36 m2 Fréjus (83)"), also used to backfill living_area_m2 when
  the badge tags don't carry a plain "36 m2" entry.
- Price lives in `.encoded-lnk` as "185 000 € *" (a "*" footnote marker
  follows the figure; must not be mistaken for a digit).
- Rooms/bedrooms/land-area badges live as sibling `<li>`/`<span>` tags
  inside `.flex.flex-wrap.gap-x-3` next to the DPE badge, e.g. "2 pièces",
  "3 chambres", "Terrain 40 m2". Any of these may be absent.
- A short description lives in `p.text-sm.text-justify` (also linked).
- Photos: images live inside `.blocMedia` (the swiper gallery). Its first
  `<img>` is a lazy-load placeholder (transparent_1x1.png / novisu fallback)
  whose real URL is only injected via an inline `<script>`; subsequent
  slides already carry a real `src` attribute. We scope to `.blocMedia` and
  skip placeholder-looking URLs so unrelated icons elsewhere in the card
  (e.g. the "last updated" clock icon) never leak into the photo list.
- Pagination: a `<div class="pgsuiv"><a href="...?p=2">Suivant</a></div>`
  is present whenever there is a next page; capped at 3 pages/city to stay
  polite (this is a very small site by our standards, so 2-3 pages already
  covers most cities of interest).

As with every other crawler here, extraction is defensive: a missing/
unexpected field yields None rather than raising, and a single malformed
card never aborts the rest of the page.
"""

import html
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.cities import CITY_METADATA, canonical_city_name, city_slug
from app.crawlers.base import BaseCrawler, CrawledListing


PARUVENDU_BASE_URL = "https://www.paruvendu.fr"
TARGET_CITIES = ["Frejus", "Saint-Raphael"]

MAX_PAGES_PER_CITY = 3

_PLACEHOLDER_IMAGE_MARKERS = ("transparent_1x1", "novisu-")

_SURFACE_RE = re.compile(r"(\d[\d\s]*)\s*m\s*2", re.IGNORECASE)
_PRICE_RE = re.compile(r"([\d][\d\s.]*)\s*€")
_DIGITS_RE = re.compile(r"(\d+)")


def _digits(value: str | None) -> int | None:
    if not value:
        return None
    match = _DIGITS_RE.search(value)
    return int(match.group(1)) if match else None


def _price(text: str) -> int | None:
    match = _PRICE_RE.search(html.unescape(text))
    if not match:
        return None
    digits = re.sub(r"\D+", "", match.group(1))
    return int(digits) if digits else None


def _surface(text: str) -> int | None:
    match = _SURFACE_RE.search(html.unescape(text))
    if not match:
        return None
    digits = re.sub(r"\D+", "", match.group(1))
    return int(digits) if digits else None


def _text(node) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _source_id(url: str) -> str | None:
    """Trailing slug segment of the detail URL, e.g.
    "/immobilier/vente/maison/1291835860A1KIVHMN000" -> "1291835860A1KIVHMN000".
    """
    if not url:
        return None
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug or None


def _extract_badges(card) -> tuple[int | None, int | None, int | None]:
    """Return (rooms, bedrooms, land_area_m2) from the badge tags next to the
    DPE indicator, e.g. ["2 pièces"], or ["4 pièces", "3 chambres",
    "Terrain 40 m2"]. Any of the three may be absent.
    """
    rooms = None
    bedrooms = None
    land_area = None

    for tag in card.select(".flex.flex-wrap.gap-x-3 > li, .flex.flex-wrap.gap-x-3 > span"):
        text = _text(tag).lower()
        if not text:
            continue
        if "terrain" in text:
            land_area = _surface(text) or _digits(text)
        elif "chambre" in text:
            bedrooms = _digits(text)
        elif "pi" in text:  # "pièce(s)" / "piece(s)"
            rooms = _digits(text)

    return rooms, bedrooms, land_area


def _extract_photos(card) -> list[str]:
    photos: list[str] = []
    media = card.select_one(".blocMedia") or card
    for img in media.select("img"):
        url = img.get("src")
        if not url:
            continue
        if any(marker in url for marker in _PLACEHOLDER_IMAGE_MARKERS):
            continue
        if url not in photos:
            photos.append(url)
    return photos[:8]


class ParuVenduCrawler(BaseCrawler):
    source = "paruvendu"

    def __init__(self, cities: list[str] | None = None) -> None:
        self.cities = sorted(set(cities or TARGET_CITIES))

    @classmethod
    def from_cities(cls, cities: list[str]) -> "ParuVenduCrawler":
        return cls(cities=cities or TARGET_CITIES)

    def _search_urls(self, city: str) -> list[str]:
        """Search URLs (page 1..MAX_PAGES_PER_CITY) for a city, or an empty
        list if the city has no known postal code in CITY_METADATA -- in
        that case the city is silently skipped (see module docstring).
        """
        canonical = canonical_city_name(city)
        metadata = CITY_METADATA.get(canonical)
        if not metadata or not metadata.get("postal_code"):
            return []

        slug = city_slug(canonical)
        postal_code = metadata["postal_code"]
        base = f"{PARUVENDU_BASE_URL}/immobilier/vente/maison/{slug}-{postal_code}/"
        return [base] + [f"{base}?p={page}" for page in range(2, MAX_PAGES_PER_CITY + 1)]

    async def crawl(self) -> list[CrawledListing]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            results: list[CrawledListing] = []
            seen: set[str] = set()
            for city in self.cities:
                canonical = canonical_city_name(city)
                metadata = CITY_METADATA.get(canonical, {})
                postal_code = metadata.get("postal_code")
                for url in self._search_urls(city):
                    response = await client.get(url)
                    response.raise_for_status()
                    page_listings = self._parse(response.text, city=canonical, postal_code=postal_code)
                    if not page_listings:
                        break
                    added_any = False
                    for listing in page_listings:
                        if listing.source_id in seen:
                            continue
                        seen.add(listing.source_id)
                        results.append(listing)
                        added_any = True
                    if not added_any:
                        break
                    if not self._has_next_page(response.text):
                        break
            return results

    def _has_next_page(self, content: str) -> bool:
        soup = BeautifulSoup(content, "html.parser")
        return soup.select_one(".pgsuiv a[href]") is not None

    def _parse(self, content: str, city: str | None = None, postal_code: str | None = None) -> list[CrawledListing]:
        soup = BeautifulSoup(content, "html.parser")
        listings: list[CrawledListing] = []
        seen: set[str] = set()

        for card in soup.select(".blocAnnonce"):
            link = card.select_one("h3 a[href]")
            href = link.get("href") if link else None
            listing_url = urljoin(PARUVENDU_BASE_URL, href) if href else None

            source_id = _source_id(href or "")
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)

            link_text = _text(link) if link else ""
            title_attr = html.unescape(link.get("title", "")).strip() if link else ""
            title = title_attr or link_text.replace("\n", " ").strip() or "Annonce ParuVendu"
            living_area = _surface(link_text) or _surface(title_attr)

            price_node = card.select_one(".encoded-lnk")
            price_eur = _price(_text(price_node)) if price_node else None

            rooms, bedrooms, land_area = _extract_badges(card)

            description_node = card.select_one("p.text-sm.text-justify")
            description = _text(description_node)

            listings.append(
                CrawledListing(
                    source=self.source,
                    source_id=source_id,
                    url=listing_url or "",
                    title=title,
                    city=city or "Unknown",
                    postal_code=postal_code,
                    price_eur=price_eur,
                    living_area_m2=living_area,
                    land_area_m2=land_area,
                    rooms=rooms,
                    bedrooms=bedrooms,
                    energy_rating=None,
                    description=description[:1200] if description else None,
                    photos=_extract_photos(card),
                )
            )

        return listings
