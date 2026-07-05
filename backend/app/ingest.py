from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cities import canonical_city_name
from app.crawlers.base import CrawledListing
from app.models import CrawlRun, Listing, ListingPhoto, ListingSource, PriceHistory
from app.scoring import score_listing

# Tolerances used by the conservative cross-source matching heuristic in
# _find_duplicate_listing: two ads are considered "the same price"/"the same
# surface" if they are exactly equal or within this relative/absolute margin.
_PRICE_MATCH_TOLERANCE_RATIO = 0.02  # +/- 2%
_LIVING_AREA_MATCH_TOLERANCE_M2 = 2  # +/- 2 sqm


def _values_match(a: int | None, b: int | None, *, tolerance_ratio: float = 0.0, tolerance_abs: float = 0.0) -> bool:
    """True if both values are present and equal within the given tolerance."""
    if a is None or b is None or a == 0 or b == 0:
        return False
    if a == b:
        return True
    diff = abs(a - b)
    allowed = max(tolerance_abs, tolerance_ratio * max(abs(a), abs(b)))
    return diff <= allowed


def _find_duplicate_listing(db: Session, item: CrawledListing) -> Listing | None:
    """Find an existing Listing that likely represents the same property.

    A single property is often advertised on both Green-Acres and Bien'ici.
    The data model supports this (one Listing -> many ListingSource), but we
    only ever want to merge when we are confident it's the same property, to
    avoid mixing up two different listings.

    Heuristic (deliberately conservative, to minimize false positives):
      - same canonical city, AND
      - price equal or within +/-2%, AND
      - living area equal or within +/-2 sqm, AND
      - both price and living area must be present (non-null, non-zero) on
        both sides -- listings missing either field are never auto-matched.

    This intentionally ignores source/source_id (those are known to differ
    across sources) and does not attempt fuzzy title matching, which is far
    more error-prone for real-estate listings that use generic wording
    ("Belle villa avec piscine").
    """
    if not item.price_eur or not item.living_area_m2:
        return None

    canonical_city = canonical_city_name(item.city)
    candidates = db.scalars(select(Listing).where(Listing.city == canonical_city))

    for candidate in candidates:
        if not candidate.price_eur or not candidate.living_area_m2:
            continue
        price_ok = _values_match(candidate.price_eur, item.price_eur, tolerance_ratio=_PRICE_MATCH_TOLERANCE_RATIO)
        area_ok = _values_match(
            candidate.living_area_m2, item.living_area_m2, tolerance_abs=_LIVING_AREA_MATCH_TOLERANCE_M2
        )
        if price_ok and area_ok:
            return candidate

    return None


def _replace_photos(db: Session, listing: Listing, urls: list[str]) -> None:
    """Replace a listing's photos with a fresh set, preserving order.

    Photo URLs served by these sites (CDN links) tend to expire, so we must
    refresh them on every crawl rather than keeping the first ones we saw.
    """
    for photo in list(listing.photos):
        db.delete(photo)
    db.flush()
    for position, url in enumerate(urls):
        db.add(ListingPhoto(listing_id=listing.id, url=url, position=position))


def upsert_listing(db: Session, item: CrawledListing) -> Listing:
    canonical_city = canonical_city_name(item.city)

    source = db.scalar(
        select(ListingSource).where(
            ListingSource.source == item.source,
            ListingSource.source_id == item.source_id,
        )
    )
    if source:
        listing = source.listing
        source.last_seen_at = datetime.utcnow()
        listing.off_market_at = None
    else:
        # New (source, source_id) pair: it might still be a listing we
        # already know about under a different source (cross-posted ad).
        listing = _find_duplicate_listing(db, item)
        if listing:
            db.add(
                ListingSource(listing_id=listing.id, source=item.source, source_id=item.source_id, url=item.url)
            )
            listing.off_market_at = None
        else:
            listing = Listing(title=item.title, city=canonical_city)
            db.add(listing)
            db.flush()
            db.add(
                ListingSource(listing_id=listing.id, source=item.source, source_id=item.source_id, url=item.url)
            )

    old_price = listing.price_eur
    listing.title = item.title
    listing.city = canonical_city
    listing.postal_code = item.postal_code
    listing.price_eur = item.price_eur
    listing.living_area_m2 = item.living_area_m2
    listing.land_area_m2 = item.land_area_m2
    listing.rooms = item.rooms
    listing.bedrooms = item.bedrooms
    listing.energy_rating = item.energy_rating
    listing.description = item.description
    listing.latitude = item.latitude
    listing.longitude = item.longitude
    listing.score = score_listing(listing)

    if item.price_eur and item.price_eur != old_price:
        db.add(PriceHistory(listing_id=listing.id, price_eur=item.price_eur))

    # Photo URLs are CDN links that expire; refresh them on every crawl that
    # yields photos instead of freezing the first ones we ever saw. If this
    # crawl didn't find any photos (e.g. transient parsing issue), leave the
    # existing ones alone rather than wiping them out.
    if item.photos:
        _replace_photos(db, listing, item.photos)

    return listing


class ExternalBatchCrawler:
    """Adapts a pre-fetched batch of listings to the BaseCrawler interface.

    External scrapers (e.g. OpenClaw, which drives a real browser to get past
    Cloudflare/DataDome on protected sources) cannot run inside this backend.
    They fetch listings themselves and POST them to the ingestion endpoint.
    This adapter lets that batch flow through the exact same `run_crawler` ->
    `upsert_listing` pipeline as any in-process crawler, so dedup, scoring,
    photo refresh, price history and CrawlRun bookkeeping stay identical.
    """

    def __init__(self, source: str, items: list[CrawledListing]) -> None:
        self.source = source
        self._items = items

    async def crawl(self) -> list[CrawledListing]:
        return self._items


async def run_crawler(db: Session, crawler) -> CrawlRun:
    run = CrawlRun(source=crawler.source, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        items = await crawler.crawl()
        for item in items:
            upsert_listing(db, item)
        run.status = "ok"
        run.found_count = len(items)
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
    finally:
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)

    return run

