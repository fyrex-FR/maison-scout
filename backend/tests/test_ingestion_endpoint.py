from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Listing, ListingSource, PriceHistory


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    settings.crawl_secret = "test-secret"
    client = TestClient(app)
    return client, SessionLocal


def _item(**overrides) -> dict:
    defaults = dict(
        source_id="pap-1",
        url="https://www.pap.fr/annonces/pap-1",
        title="Maison avec jardin",
        city="Frejus",
        postal_code="83600",
        price_eur=450000,
        living_area_m2=120,
        land_area_m2=500,
        rooms=5,
        bedrooms=3,
        energy_rating="C",
        description="Belle maison lumineuse.",
        photos=["https://cdn.pap.fr/1.jpg", "https://cdn.pap.fr/2.jpg"],
        latitude=43.433,
        longitude=6.735,
    )
    defaults.update(overrides)
    return defaults


HEADERS = {"X-Crawl-Secret": "test-secret"}


def test_ingest_listings_requires_crawl_secret():
    client, _SessionLocal = _client()
    response = client.post("/api/ingest/listings", json={"source": "pap", "items": [_item()]})
    assert response.status_code == 401


def test_ingest_listings_creates_listings_with_source_photos_and_geo():
    client, SessionLocal = _client()
    payload = {
        "source": "pap",
        "items": [
            _item(source_id="pap-1", title="Maison A"),
            _item(source_id="pap-2", title="Maison B", price_eur=600000, living_area_m2=200),
        ],
    }

    response = client.post("/api/ingest/listings", headers=HEADERS, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["source"] == "pap"
    assert body["found_count"] == 2
    assert body["error"] is None

    with SessionLocal() as db:
        assert db.query(Listing).count() == 2
        sources = db.scalars(select(ListingSource).where(ListingSource.source == "pap")).all()
        assert {s.source_id for s in sources} == {"pap-1", "pap-2"}

        listing_a = db.scalar(
            select(Listing).join(ListingSource).where(ListingSource.source_id == "pap-1")
        )
        assert listing_a.title == "Maison A"
        assert listing_a.latitude == 43.433
        assert listing_a.longitude == 6.735
        assert [p.url for p in listing_a.photos] == [
            "https://cdn.pap.fr/1.jpg",
            "https://cdn.pap.fr/2.jpg",
        ]


def test_ingest_listings_rejects_batch_over_500_items():
    client, _SessionLocal = _client()
    items = [_item(source_id=f"pap-{i}") for i in range(501)]
    response = client.post("/api/ingest/listings", headers=HEADERS, json={"source": "pap", "items": items})
    assert response.status_code == 400


def test_ingest_listings_rejects_empty_items():
    client, _SessionLocal = _client()
    response = client.post("/api/ingest/listings", headers=HEADERS, json={"source": "pap", "items": []})
    assert response.status_code == 400


def test_ingest_listings_reingesting_same_source_id_upserts_and_records_price_drop():
    client, SessionLocal = _client()

    first = client.post(
        "/api/ingest/listings",
        headers=HEADERS,
        json={"source": "pap", "items": [_item(source_id="pap-1", price_eur=450000)]},
    )
    assert first.status_code == 200
    assert first.json()["found_count"] == 1

    second = client.post(
        "/api/ingest/listings",
        headers=HEADERS,
        json={"source": "pap", "items": [_item(source_id="pap-1", price_eur=430000)]},
    )
    assert second.status_code == 200
    assert second.json()["found_count"] == 1

    with SessionLocal() as db:
        # Upsert, not a duplicate: still a single Listing / ListingSource pair.
        assert db.query(Listing).count() == 1
        assert db.query(ListingSource).count() == 1

        listing = db.scalar(select(Listing))
        assert listing.price_eur == 430000

        price_points = db.scalars(
            select(PriceHistory.price_eur).where(PriceHistory.listing_id == listing.id)
        ).all()
        assert 430000 in price_points
