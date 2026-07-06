from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.crawlers.pap import PapCrawler
from app.db import Base, get_db
from app.main import app
from app.models import Listing, ListingPhoto, ListingSource, SearchProfile, User


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


def _listing(db: Session, source_id: str = "ga-1") -> Listing:
    listing = Listing(
        title="Villa avec piscine",
        city="Frejus",
        postal_code="83600",
        price_eur=750000,
        living_area_m2=150,
        land_area_m2=800,
        bedrooms=4,
        description="Maison avec séjour ouvert sur terrasse et piscine.",
        status="new",
    )
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id=source_id, url=f"https://ga/{source_id}"))
    db.add(ListingPhoto(listing_id=listing.id, url=f"https://cdn/{source_id}.jpg", position=0))
    db.commit()
    db.refresh(listing)
    return listing


def test_new_user_sees_all_existing_listings_as_new():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        assert user.listings_seen_at is None
        _listing(db, "ga-1")
        _listing(db, "ga-2")
        db.commit()
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert all(item["is_new"] is True for item in payload)


def test_mark_seen_then_existing_listings_are_no_longer_new_but_later_ones_are():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        _listing(db, "ga-old")
        db.commit()
        token = create_token(user)

    headers = {"Authorization": f"Bearer {token}"}

    mark_response = client.post("/api/listings/mark-seen", headers=headers)
    assert mark_response.status_code == 200
    body = mark_response.json()
    assert body["status"] == "ok"
    assert "listings_seen_at" in body
    # ISO-format timestamp, parseable.
    datetime.fromisoformat(body["listings_seen_at"])

    with SessionLocal() as db:
        stored_user = db.get(User, 1)
        assert stored_user.listings_seen_at is not None

    response = client.get("/api/listings", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["is_new"] is False

    # A listing created after the mark-seen watermark must show up as new.
    with SessionLocal() as db:
        new_listing = _listing(db, "ga-new")
        new_listing.created_at = datetime.utcnow() + timedelta(minutes=5)
        db.commit()

    response = client.get("/api/listings", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    is_new_by_id = {item["id"]: item["is_new"] for item in payload}
    # Both listings share the same title in this fixture; disambiguate by id
    # instead -- the freshly created listing has the higher id.
    new_id = max(is_new_by_id)
    old_id = min(is_new_by_id)
    assert is_new_by_id[new_id] is True
    assert is_new_by_id[old_id] is False


def test_mark_seen_requires_authentication():
    client, _SessionLocal = _client()
    response = client.post("/api/listings/mark-seen")
    assert response.status_code == 401


def test_crawl_pap_requires_crawl_access():
    client, _SessionLocal = _client()
    response = client.post("/api/crawl/pap")
    assert response.status_code == 401


def test_crawl_pap_succeeds_with_crawl_secret(monkeypatch):
    async def _fake_crawl(self):
        return []

    monkeypatch.setattr(PapCrawler, "crawl", _fake_crawl)

    client, _SessionLocal = _client()
    response = client.post("/api/crawl/pap", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["found_count"] == 0


def test_crawl_pap_enqueued_as_openclaw_job_by_crawl_all(monkeypatch):
    """PAP is behind Cloudflare and can't run in-process: /api/crawl/all now
    enqueues it as an "openclaw"-executor CrawlJob (left pending for the
    external browser worker to claim -- see app/crawl_jobs.py) instead of
    triggering PapCrawler directly or excluding it outright.
    """
    from app.crawlers.bien_ici import BienIciCrawler
    from app.crawlers.green_acres import GreenAcresCrawler
    from app.crawlers.paruvendu import ParuVenduCrawler

    async def _fake_crawl(self):
        return []

    monkeypatch.setattr(GreenAcresCrawler, "crawl", _fake_crawl)
    monkeypatch.setattr(BienIciCrawler, "crawl", _fake_crawl)
    monkeypatch.setattr(ParuVenduCrawler, "crawl", _fake_crawl)

    client, _SessionLocal = _client()
    response = client.post("/api/crawl/all", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    pap_jobs = [job for job in payload["created"] if job["source"] == "pap"]
    assert len(pap_jobs) == 1
    assert pap_jobs[0]["executor"] == "openclaw"
    assert pap_jobs[0]["status"] == "pending"
