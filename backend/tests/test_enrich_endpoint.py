"""API-level tests for POST /api/enrich/all.

Monkeypatches the I/O functions (app.enrichment.dvf.refresh_city_stats /
app.enrichment.georisques.enrich_listings_risks as imported into app.main) so
no real network call happens in tests. Covers auth (401 without secret or
bearer) and that the two passes' counters flow through to the response.
"""

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

import app.main as main
from app.config import settings
from app.db import Base, get_db
from app.main import app


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


def test_enrich_all_requires_crawl_access():
    client, _SessionLocal = _client()
    response = client.post("/api/enrich/all")
    assert response.status_code == 401


def test_enrich_all_rejects_wrong_secret():
    client, _SessionLocal = _client()
    response = client.post("/api/enrich/all", headers={"X-Crawl-Secret": "nope"})
    assert response.status_code == 401


async def _fake_refresh_city_stats(db, cities):
    return {"refreshed": len(cities), "skipped": 0, "failed": 0}


async def _fake_enrich_listings_risks(db, *, limit=40):
    return {"checked": 3, "failed": 1}


def test_enrich_all_returns_counters_from_both_passes(monkeypatch):
    client, _SessionLocal = _client()
    monkeypatch.setattr(main, "refresh_city_stats", _fake_refresh_city_stats)
    monkeypatch.setattr(main, "enrich_listings_risks", _fake_enrich_listings_risks)

    response = client.post("/api/enrich/all", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    body = response.json()
    assert body["dvf"] == {"refreshed": 0, "skipped": 0, "failed": 0}
    assert body["risks"] == {"checked": 3, "failed": 1}


async def _boom_refresh_city_stats(db, cities):
    raise RuntimeError("dvf source unreachable")


def test_enrich_all_never_500s_on_external_source_failure(monkeypatch):
    client, _SessionLocal = _client()
    monkeypatch.setattr(main, "refresh_city_stats", _boom_refresh_city_stats)
    monkeypatch.setattr(main, "enrich_listings_risks", _fake_enrich_listings_risks)

    response = client.post("/api/enrich/all", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    body = response.json()
    assert body["dvf"]["failed"] == 0  # our fake never increments "failed", but "error" key must be present
    assert "error" in body["dvf"]
    assert body["risks"] == {"checked": 3, "failed": 1}


def test_enrich_all_accepts_valid_bearer_token():
    client, SessionLocal = _client()
    from app.auth import create_token
    from app.models import User

    with SessionLocal() as db:
        user = User(email="a@example.com", display_name="a", password_hash="hash")
        db.add(user)
        db.commit()
        token = create_token(user)

    response = client.post("/api/enrich/all", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
