from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.config import settings
from app.crawlers.paruvendu import ParuVenduCrawler
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


def test_crawl_paruvendu_requires_crawl_access():
    client, _SessionLocal = _client()
    response = client.post("/api/crawl/paruvendu")
    assert response.status_code == 401


def test_crawl_paruvendu_succeeds_with_crawl_secret(monkeypatch):
    async def _fake_crawl(self):
        return []

    monkeypatch.setattr(ParuVenduCrawler, "crawl", _fake_crawl)

    client, _SessionLocal = _client()
    response = client.post("/api/crawl/paruvendu", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["found_count"] == 0


def test_crawl_paruvendu_included_in_crawl_all_job_queue(monkeypatch):
    """ParuVendu was validated in prod (191 listings) and now runs in the
    normal backend rotation: /api/crawl/all must enqueue a "backend"-executor
    job for it (fed to the in-process job queue -- see app/crawl_jobs.py),
    alongside green-acres and bien-ici.
    """
    from app.crawlers.bien_ici import BienIciCrawler
    from app.crawlers.green_acres import GreenAcresCrawler

    async def _fake_crawl(self):
        return []

    monkeypatch.setattr(GreenAcresCrawler, "crawl", _fake_crawl)
    monkeypatch.setattr(BienIciCrawler, "crawl", _fake_crawl)
    monkeypatch.setattr(ParuVenduCrawler, "crawl", _fake_crawl)

    client, _SessionLocal = _client()
    response = client.post("/api/crawl/all", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    created_sources = {job["source"] for job in payload["created"]}
    assert "paruvendu" in created_sources
    assert {"green-acres", "bien-ici", "paruvendu"} <= created_sources
