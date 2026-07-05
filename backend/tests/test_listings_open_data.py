"""API-level tests: GET /api/listings exposes dvf_* fields and risks.

Seeds a CityMarketStat row (DVF cache) and a listing with georisques_json to
verify the enrichment values flow through attach_user_context into
ListingOut without hitting any real network.
"""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import CityMarketStat, Listing, ListingSource, SearchProfile, User


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


def _user(db: Session, email: str = "x@example.com") -> User:
    user = User(email=email, display_name=email.split("@")[0], password_hash="hash")
    db.add(user)
    db.flush()
    return user


def _allow_frejus(db: Session, user: User) -> None:
    db.add(SearchProfile(user_id=user.id, name="Frejus", city="Frejus"))
    db.commit()


def _listing(db: Session, source_id: str, **overrides) -> Listing:
    defaults = dict(
        title=f"Villa {source_id}",
        city="Frejus",
        postal_code="83600",
        price_eur=400000,
        living_area_m2=100,
        status="new",
    )
    defaults.update(overrides)
    listing = Listing(**defaults)
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id=source_id, url=f"https://ga/{source_id}"))
    db.commit()
    db.refresh(listing)
    return listing


def test_listings_expose_dvf_fields_and_delta_ratio():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        db.add(
            CityMarketStat(
                city="Frejus",
                insee_code="83061",
                median_price_per_m2_house=3000.0,
                sample_count=42,
                period_label="DVF 2024–2025",
                computed_at=datetime.utcnow(),
            )
        )
        db.commit()
        # price_eur=400000, living_area_m2=100 -> 4000 EUR/m2 vs 3000 median -> +0.333
        listing = _listing(db, "dvf-1", price_eur=400000, living_area_m2=100)
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    item = next(i for i in response.json() if i["id"] == listing_id)
    assert item["dvf_median_price_per_m2"] == 3000.0
    assert item["dvf_period"] == "DVF 2024–2025"
    assert item["dvf_delta_ratio"] == round((4000.0 / 3000.0) - 1, 3)
    codes = [f["code"] for f in item["auto_flags"]]
    assert "price_above_market_sales" in codes


def test_listings_expose_risks_from_georisques_json():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = _listing(
            db,
            "risk-1",
            georisques_json={"inondation": True, "argiles": False},
            georisques_checked_at=datetime.utcnow(),
        )
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    item = next(i for i in response.json() if i["id"] == listing_id)
    assert item["risks"] == {"inondation": True, "argiles": False}
    codes = [f["code"] for f in item["auto_flags"]]
    assert "flood_risk" in codes
    assert "clay_risk" not in codes


def test_listings_without_market_stat_or_risks_have_none_fields():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = _listing(db, "plain-1")
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    item = next(i for i in response.json() if i["id"] == listing_id)
    assert item["dvf_median_price_per_m2"] is None
    assert item["dvf_period"] is None
    assert item["dvf_delta_ratio"] is None
    assert item["risks"] is None


def test_dvf_delta_ratio_none_when_price_or_area_missing():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        db.add(
            CityMarketStat(
                city="Frejus",
                median_price_per_m2_house=3000.0,
                sample_count=10,
                period_label="DVF 2024",
                computed_at=datetime.utcnow(),
            )
        )
        db.commit()
        listing = _listing(db, "no-area-1", living_area_m2=None)
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    item = next(i for i in response.json() if i["id"] == listing_id)
    assert item["dvf_median_price_per_m2"] == 3000.0
    assert item["dvf_delta_ratio"] is None
