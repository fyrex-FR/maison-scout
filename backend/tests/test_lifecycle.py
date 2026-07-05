"""Unit tests for app.lifecycle (off-market detection + resurrection)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawlers.base import CrawledListing
from app.db import Base
from app.ingest import upsert_listing
from app.lifecycle import refresh_off_market_status
from app.models import Listing, ListingSource


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _listing(**overrides):
    defaults = dict(
        source="green-acres",
        source_id="ga-1",
        url="http://ga/1",
        title="Villa GA",
        city="Frejus",
        postal_code="83600",
        price_eur=450000,
        living_area_m2=120,
        photos=["http://cdn/1.jpg"],
    )
    defaults.update(overrides)
    return CrawledListing(**defaults)


def _make_listing_with_source(db, *, last_seen_at: datetime, source="green-acres", source_id="ga-1") -> Listing:
    listing = Listing(title="Villa", city="Frejus", price_eur=400000, living_area_m2=100)
    db.add(listing)
    db.flush()
    db.add(
        ListingSource(
            listing_id=listing.id,
            source=source,
            source_id=source_id,
            url="http://x/1",
            first_seen_at=last_seen_at,
            last_seen_at=last_seen_at,
        )
    )
    db.commit()
    db.refresh(listing)
    return listing


def test_listing_marked_off_market_after_threshold(db):
    now = datetime(2026, 7, 5, 12, 0, 0)
    stale_seen_at = now - timedelta(hours=49)
    listing = _make_listing_with_source(db, last_seen_at=stale_seen_at)

    result = refresh_off_market_status(db, now=now)
    db.commit()
    db.refresh(listing)

    assert result == {"marked_off_market": 1}
    assert listing.off_market_at == now


def test_listing_not_marked_before_threshold(db):
    now = datetime(2026, 7, 5, 12, 0, 0)
    fresh_seen_at = now - timedelta(hours=47)
    listing = _make_listing_with_source(db, last_seen_at=fresh_seen_at)

    result = refresh_off_market_status(db, now=now)
    db.commit()
    db.refresh(listing)

    assert result == {"marked_off_market": 0}
    assert listing.off_market_at is None


def test_multi_source_listing_stays_active_if_one_source_is_fresh(db):
    now = datetime(2026, 7, 5, 12, 0, 0)
    listing = Listing(title="Villa multi", city="Frejus", price_eur=400000, living_area_m2=100)
    db.add(listing)
    db.flush()
    db.add(
        ListingSource(
            listing_id=listing.id,
            source="green-acres",
            source_id="ga-multi",
            url="http://ga/multi",
            last_seen_at=now - timedelta(hours=100),  # very stale
        )
    )
    db.add(
        ListingSource(
            listing_id=listing.id,
            source="bien-ici",
            source_id="bi-multi",
            url="http://bi/multi",
            last_seen_at=now - timedelta(hours=1),  # fresh
        )
    )
    db.commit()

    result = refresh_off_market_status(db, now=now)
    db.commit()
    db.refresh(listing)

    assert result == {"marked_off_market": 0}
    assert listing.off_market_at is None


def test_listing_without_any_source_is_ignored(db):
    now = datetime(2026, 7, 5, 12, 0, 0)
    listing = Listing(title="Sans source", city="Frejus", price_eur=400000, living_area_m2=100)
    db.add(listing)
    db.commit()

    result = refresh_off_market_status(db, now=now)
    db.commit()
    db.refresh(listing)

    assert result == {"marked_off_market": 0}
    assert listing.off_market_at is None


def test_already_off_market_listing_is_not_recounted(db):
    now = datetime(2026, 7, 5, 12, 0, 0)
    listing = _make_listing_with_source(db, last_seen_at=now - timedelta(hours=200))
    listing.off_market_at = now - timedelta(hours=100)
    db.commit()

    result = refresh_off_market_status(db, now=now)
    db.commit()

    assert result == {"marked_off_market": 0}


def test_resurrection_via_upsert_listing_existing_source(db):
    now = datetime(2026, 7, 5, 12, 0, 0)
    stale_seen_at = now - timedelta(hours=200)
    listing = _make_listing_with_source(db, last_seen_at=stale_seen_at, source="green-acres", source_id="ga-1")
    listing.off_market_at = now - timedelta(hours=1)
    db.commit()

    revived = upsert_listing(db, _listing(source="green-acres", source_id="ga-1"))
    db.commit()

    assert revived.id == listing.id
    assert revived.off_market_at is None


def test_resurrection_via_upsert_listing_new_cross_source_match(db):
    # Off-market listing whose only source is stale; a new source for the
    # exact same property (matched via the cross-source dedup heuristic)
    # should bring it back to life.
    listing1 = upsert_listing(db, _listing(source="green-acres", source_id="ga-1"))
    db.commit()
    listing1.off_market_at = datetime.utcnow()
    db.commit()

    listing2 = upsert_listing(
        db,
        _listing(source="bien-ici", source_id="bi-99", price_eur=450500, living_area_m2=121, photos=[]),
    )
    db.commit()

    assert listing1.id == listing2.id
    assert listing2.off_market_at is None
