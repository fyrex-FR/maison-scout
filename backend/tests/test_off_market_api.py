"""API-level tests for the off-market lifecycle feature.

Covers: default exclusion of off-market listings, the favorite/call
exception, include_off_market=true, and the off_market/off_market_at/
days_on_market fields on ListingOut.
"""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Listing, ListingPhoto, ListingSource, SearchProfile, User, UserListingState


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


def _listing(db: Session, source_id: str, *, off_market_at=None, created_at=None) -> Listing:
    listing = Listing(
        title=f"Villa {source_id}",
        city="Frejus",
        postal_code="83600",
        price_eur=400000,
        living_area_m2=100,
        status="new",
        off_market_at=off_market_at,
    )
    if created_at is not None:
        listing.created_at = created_at
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id=source_id, url=f"https://ga/{source_id}"))
    db.add(ListingPhoto(listing_id=listing.id, url=f"https://cdn/{source_id}.jpg", position=0))
    db.commit()
    db.refresh(listing)
    return listing


def test_off_market_listing_excluded_by_default():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        _listing(db, "active-1")
        _listing(db, "off-1", off_market_at=datetime.utcnow())
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    titles = {item["title"] for item in payload}
    assert titles == {"Villa active-1"}


def test_off_market_favorite_stays_visible_marked_off_market():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        off_listing = _listing(db, "off-fav", off_market_at=datetime.utcnow())
        db.add(UserListingState(user_id=user.id, listing_id=off_listing.id, status="favorite"))
        db.commit()
        listing_id = off_listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == listing_id
    assert payload[0]["off_market"] is True


def test_off_market_call_status_also_stays_visible():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        off_listing = _listing(db, "off-call", off_market_at=datetime.utcnow())
        db.add(UserListingState(user_id=user.id, listing_id=off_listing.id, status="call"))
        db.commit()
        listing_id = off_listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == listing_id


def test_off_market_rejected_status_does_not_grant_visibility():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        off_listing = _listing(db, "off-rejected", off_market_at=datetime.utcnow())
        db.add(UserListingState(user_id=user.id, listing_id=off_listing.id, status="rejected"))
        db.commit()
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload == []


def test_include_off_market_true_returns_everything():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        _listing(db, "active-2")
        _listing(db, "off-2", off_market_at=datetime.utcnow())
        token = create_token(user)

    response = client.get(
        "/api/listings",
        params={"include_off_market": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    titles = {item["title"] for item in payload}
    assert titles == {"Villa active-2", "Villa off-2"}


def test_active_listing_fields_off_market_false_and_days_on_market_from_now():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        created = datetime.utcnow() - timedelta(days=10)
        listing = _listing(db, "days-active", created_at=created)
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    item = next(item for item in payload if item["id"] == listing_id)
    assert item["off_market"] is False
    assert item["off_market_at"] is None
    assert item["days_on_market"] == 10


def test_off_market_listing_fields_days_on_market_frozen_at_off_market_at():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        created = datetime.utcnow() - timedelta(days=30)
        off_at = created + timedelta(days=12)
        off_listing = _listing(db, "days-off", off_market_at=off_at, created_at=created)
        db.add(UserListingState(user_id=user.id, listing_id=off_listing.id, status="favorite"))
        db.commit()
        listing_id = off_listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    item = next(item for item in payload if item["id"] == listing_id)
    assert item["off_market"] is True
    assert item["off_market_at"] is not None
    assert item["days_on_market"] == 12


def test_long_on_market_auto_flag_only_shown_for_active_listing_past_threshold():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        created = datetime.utcnow() - timedelta(days=90)
        _listing(db, "long-active", created_at=created)
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    item = payload[0]
    codes = [f["code"] for f in item["auto_flags"]]
    assert "long_on_market" in codes


def test_long_on_market_auto_flag_not_shown_for_off_market_listing():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        created = datetime.utcnow() - timedelta(days=200)
        off_listing = _listing(db, "long-off", off_market_at=datetime.utcnow(), created_at=created)
        db.add(UserListingState(user_id=user.id, listing_id=off_listing.id, status="favorite"))
        db.commit()
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    item = payload[0]
    codes = [f["code"] for f in item["auto_flags"]]
    assert "long_on_market" not in codes
