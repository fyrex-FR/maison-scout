from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crawlers.base import CrawledListing
from app.models import CrawlRun, Listing, ListingPhoto, ListingSource, PriceHistory
from app.scoring import score_listing


def upsert_listing(db: Session, item: CrawledListing) -> Listing:
    source = db.scalar(
        select(ListingSource).where(
            ListingSource.source == item.source,
            ListingSource.source_id == item.source_id,
        )
    )
    if source:
        listing = source.listing
        source.last_seen_at = datetime.utcnow()
    else:
        listing = Listing(title=item.title, city=item.city)
        db.add(listing)
        db.flush()
        db.add(ListingSource(listing_id=listing.id, source=item.source, source_id=item.source_id, url=item.url))

    old_price = listing.price_eur
    listing.title = item.title
    listing.city = item.city
    listing.postal_code = item.postal_code
    listing.price_eur = item.price_eur
    listing.living_area_m2 = item.living_area_m2
    listing.land_area_m2 = item.land_area_m2
    listing.rooms = item.rooms
    listing.bedrooms = item.bedrooms
    listing.energy_rating = item.energy_rating
    listing.description = item.description
    listing.score = score_listing(listing)

    if item.price_eur and item.price_eur != old_price:
        db.add(PriceHistory(listing_id=listing.id, price_eur=item.price_eur))

    if item.photos and not listing.photos:
        for position, url in enumerate(item.photos):
            db.add(ListingPhoto(listing_id=listing.id, url=url, position=position))

    return listing


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

