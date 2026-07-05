from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Listing, ListingPhoto, ListingSource, PriceHistory, SearchProfile, User


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
    """/api/listings only returns listings whose city matches an enabled
    SearchProfile for the user; add a permissive one so these tests can focus
    on the insights fields without being filtered out by city rules.
    """
    db.add(SearchProfile(user_id=user.id, name="Frejus", city="Frejus"))
    db.commit()


def _listing(db: Session, source_id: str = "ga-1", **overrides) -> Listing:
    defaults = dict(
        title="Villa avec piscine",
        city="Frejus",
        postal_code="83600",
        price_eur=750000,
        living_area_m2=150,
        land_area_m2=800,
        bedrooms=4,
        energy_rating="C",
        description="Maison avec séjour ouvert sur terrasse et piscine.",
        status="new",
    )
    defaults.update(overrides)
    listing = Listing(**defaults)
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id=source_id, url=f"https://ga/{source_id}"))
    db.add(ListingPhoto(listing_id=listing.id, url=f"https://cdn/{source_id}.jpg", position=0))
    db.commit()
    db.refresh(listing)
    return listing


def test_api_listings_exposes_auto_flags_for_a_flagged_listing():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = Listing(
            title="Maison a renover",
            city="Frejus",
            postal_code="83600",
            price_eur=500000,
            living_area_m2=100,
            land_area_m2=500,
            bedrooms=3,
            energy_rating="G",
            description="A rafraichir",
            status="new",
        )
        db.add(listing)
        db.flush()
        db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id="ga-flagged", url="https://ga/ga-flagged"))
        # No photo on purpose -> triggers "no_photos".
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == listing_id
    codes = {flag["code"] for flag in item["auto_flags"]}
    assert "dpe_poor" in codes
    assert "no_photos" in codes


def test_api_listings_auto_flags_empty_for_a_clean_listing():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        _listing(db, "ga-clean")
        db.commit()
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["auto_flags"] == []


def test_api_listings_reports_price_drop_from_price_history():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = _listing(db, "ga-dropping", price_eur=450000)
        now = datetime.utcnow()
        db.add(PriceHistory(listing_id=listing.id, price_eur=500000, observed_at=now - timedelta(days=20)))
        db.add(PriceHistory(listing_id=listing.id, price_eur=480000, observed_at=now - timedelta(days=10)))
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == listing_id
    assert item["price_dropped"] is True
    assert item["price_change_abs"] == 450000 - 500000
    assert item["price_observations"] == 3


def test_api_listings_price_dropped_false_when_price_stable_or_rising():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        stable = _listing(db, "ga-stable", price_eur=500000)
        rising = _listing(db, "ga-rising", price_eur=520000)
        now = datetime.utcnow()
        # Stable: last historical price already equals the current price, so
        # per price_insight's contract it is not appended again (single-point
        # series -> change_abs is None, not a spurious 0).
        db.add(PriceHistory(listing_id=stable.id, price_eur=500000, observed_at=now - timedelta(days=5)))
        # Rising: an earlier, lower price followed by the current higher price.
        db.add(PriceHistory(listing_id=rising.id, price_eur=500000, observed_at=now - timedelta(days=5)))
        db.commit()
        stable_id, rising_id = stable.id, rising.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = {item["id"]: item for item in response.json()}
    assert payload[stable_id]["price_dropped"] is False
    assert payload[stable_id]["price_change_abs"] is None
    assert payload[stable_id]["price_observations"] == 1
    assert payload[rising_id]["price_dropped"] is False
    assert payload[rising_id]["price_change_abs"] == 20000
    assert payload[rising_id]["price_observations"] == 2


def test_price_history_endpoint_returns_points_sorted_chronologically():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        listing = _listing(db, "ga-history")
        now = datetime.utcnow()
        db.add(PriceHistory(listing_id=listing.id, price_eur=520000, observed_at=now - timedelta(days=30)))
        db.add(PriceHistory(listing_id=listing.id, price_eur=500000, observed_at=now - timedelta(days=15)))
        db.add(PriceHistory(listing_id=listing.id, price_eur=490000, observed_at=now - timedelta(days=1)))
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get(
        f"/api/listings/{listing_id}/price-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert [point["price_eur"] for point in payload] == [520000, 500000, 490000]
    observed_ats = [point["observed_at"] for point in payload]
    assert observed_ats == sorted(observed_ats)


def test_price_history_endpoint_404_for_unknown_listing():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        db.commit()
        token = create_token(user)

    response = client.get(
        "/api/listings/999999/price-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_price_history_endpoint_requires_auth():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        listing = _listing(db, "ga-noauth")
        listing_id = listing.id

    response = client.get(f"/api/listings/{listing_id}/price-history")
    assert response.status_code == 401
