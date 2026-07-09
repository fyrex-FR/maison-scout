import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawlers.base import CrawledListing
from app.db import Base
from app.ingest import upsert_listing
from app.models import Listing, ListingSource


@pytest.fixture()
def db():
    # Isolated in-memory SQLite DB per test; independent of the app's
    # configured (Postgres) database_url.
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
        city="Fréjus",
        postal_code="83600",
        price_eur=450000,
        living_area_m2=120,
        photos=["http://cdn/1.jpg", "http://cdn/2.jpg"],
    )
    defaults.update(overrides)
    return CrawledListing(**defaults)


def test_city_is_stored_canonically(db):
    listing = upsert_listing(db, _listing(city="Fréjus"))
    db.commit()
    assert listing.city == "Frejus"


def test_cross_source_match_attaches_new_source_instead_of_duplicating(db):
    listing1 = upsert_listing(db, _listing(source="green-acres", source_id="ga-1"))
    db.commit()

    # Same property advertised on Bien'ici: same city, price within 2%,
    # living area within 2 sqm -> should merge into the same Listing.
    listing2 = upsert_listing(
        db,
        _listing(source="bien-ici", source_id="bi-99", price_eur=450500, living_area_m2=121, photos=[]),
    )
    db.commit()

    assert listing1.id == listing2.id
    assert db.query(Listing).count() == 1
    assert db.query(ListingSource).count() == 2


def test_dissimilar_listing_is_not_merged(db):
    listing1 = upsert_listing(db, _listing(source="green-acres", source_id="ga-1"))
    db.commit()

    listing2 = upsert_listing(
        db,
        _listing(source="bien-ici", source_id="bi-100", price_eur=900000, living_area_m2=250, photos=[]),
    )
    db.commit()

    assert listing1.id != listing2.id
    assert db.query(Listing).count() == 2


def test_missing_price_or_area_prevents_auto_merge(db):
    listing1 = upsert_listing(db, _listing(source="green-acres", source_id="ga-1"))
    db.commit()

    # Same city but no price/area to compare -> must not be merged blindly.
    listing2 = upsert_listing(
        db,
        _listing(source="bien-ici", source_id="bi-101", price_eur=None, living_area_m2=None, photos=[]),
    )
    db.commit()

    assert listing1.id != listing2.id


def test_photos_are_refreshed_on_each_crawl_with_new_photos(db):
    listing = upsert_listing(db, _listing(photos=["http://cdn/1.jpg", "http://cdn/2.jpg"]))
    db.commit()
    assert [p.url for p in listing.photos] == ["http://cdn/1.jpg", "http://cdn/2.jpg"]

    listing = upsert_listing(db, _listing(photos=["http://cdn/new1.jpg"]))
    db.commit()
    assert [p.url for p in listing.photos] == ["http://cdn/new1.jpg"]


def test_photos_are_kept_when_new_crawl_has_no_photos(db):
    listing = upsert_listing(db, _listing(photos=["http://cdn/1.jpg"]))
    db.commit()

    listing = upsert_listing(db, _listing(photos=[]))
    db.commit()

    assert [p.url for p in listing.photos] == ["http://cdn/1.jpg"]


def test_existing_source_url_is_refreshed_when_crawl_has_detail_url(db):
    upsert_listing(
        db,
        _listing(
            source="logic-immo",
            source_id="logic-1",
            url="https://www.logic-immo.com/",
        ),
    )
    db.commit()

    upsert_listing(
        db,
        _listing(
            source="logic-immo",
            source_id="logic-1",
            url="https://www.logic-immo.com/detail-vente-1234567890.htm",
        ),
    )
    db.commit()

    source = db.query(ListingSource).filter_by(source="logic-immo", source_id="logic-1").one()
    assert source.url == "https://www.logic-immo.com/detail-vente-1234567890.htm"


def test_existing_source_url_is_not_replaced_by_logic_immo_homepage(db):
    upsert_listing(
        db,
        _listing(
            source="logic-immo",
            source_id="logic-1",
            url="https://www.logic-immo.com/detail-vente-1234567890.htm",
        ),
    )
    db.commit()

    upsert_listing(
        db,
        _listing(
            source="logic-immo",
            source_id="logic-1",
            url="https://www.logic-immo.com/",
        ),
    )
    db.commit()

    source = db.query(ListingSource).filter_by(source="logic-immo", source_id="logic-1").one()
    assert source.url == "https://www.logic-immo.com/detail-vente-1234567890.htm"
