"""API tests for the crawl job queue endpoints.

Covers POST /api/crawl/request, POST /api/crawl/all (now an alias), and the
GET/POST /api/crawl/jobs* worker-facing endpoints. See app/crawl_jobs.py for
the underlying enqueue/claim/report logic and docs/PROJECT_CONTEXT.md for the
architecture (backend as control plane, backend-executor jobs run in
FastAPI BackgroundTasks, openclaw-executor jobs are pulled by the external
worker). No network calls: the httpx crawlers are monkeypatched to no-op.
"""

import time

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.crawlers.bien_ici import BienIciCrawler
from app.crawlers.green_acres import GreenAcresCrawler
from app.crawlers.paruvendu import ParuVenduCrawler
from app.db import Base, get_db
from app.models import User
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


def _user(db: Session, email: str = "x@example.com") -> User:
    user = User(email=email, display_name=email.split("@")[0], password_hash="hash")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _noop_backend_crawlers(monkeypatch):
    async def _fake_crawl(self):
        return []

    monkeypatch.setattr(GreenAcresCrawler, "crawl", _fake_crawl)
    monkeypatch.setattr(BienIciCrawler, "crawl", _fake_crawl)
    monkeypatch.setattr(ParuVenduCrawler, "crawl", _fake_crawl)


# --- POST /api/crawl/request --------------------------------------------------


def test_crawl_request_requires_auth():
    client, _SessionLocal = _client()
    response = client.post("/api/crawl/request")
    assert response.status_code == 401


def test_crawl_request_with_bearer_token_succeeds(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        token = create_token(user)

    response = client.post(
        "/api/crawl/request",
        json={"sources": ["green-acres"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["created"]) == 1
    assert payload["created"][0]["source"] == "green-acres"
    assert payload["created"][0]["requested_by"] == user.email


def test_crawl_request_with_secret_succeeds(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, _SessionLocal = _client()

    response = client.post(
        "/api/crawl/request",
        json={"sources": ["seloger"]},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["created"][0]["source"] == "seloger"
    assert payload["created"][0]["requested_by"] == "crawl-secret"
    assert payload["created"][0]["executor"] == "openclaw"
    assert payload["created"][0]["status"] == "pending"


def test_crawl_request_no_sources_means_all(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, _SessionLocal = _client()

    response = client.post("/api/crawl/request", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    from app.crawl_jobs import SOURCE_EXECUTORS

    assert {job["source"] for job in payload["created"]} == set(SOURCE_EXECUTORS.keys())


def test_crawl_request_backend_jobs_processed_by_background_task(monkeypatch):
    """Backend-executor jobs get claimed and run by the BackgroundTask; with
    TestClient, BackgroundTasks execute synchronously after the response body
    is produced but before the request context manager exits -- give it a
    brief moment then check the job reached a terminal state.
    """
    _noop_backend_crawlers(monkeypatch)
    client, SessionLocal = _client()

    response = client.post(
        "/api/crawl/request",
        json={"sources": ["green-acres", "seloger"]},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        from app.models import CrawlJob

        for _ in range(20):
            jobs = list(db.query(CrawlJob).all())
            ga_job = next(j for j in jobs if j.source == "green-acres")
            if ga_job.status != "pending":
                break
            db.expire_all()
            time.sleep(0.05)
        else:
            ga_job = next(j for j in db.query(CrawlJob).all() if j.source == "green-acres")

        seloger_job = next(j for j in db.query(CrawlJob).all() if j.source == "seloger")

    assert ga_job.status == "done"
    assert ga_job.found_count == 0
    assert ga_job.crawl_run_id is not None
    # openclaw jobs are never touched by the backend's BackgroundTask.
    assert seloger_job.status == "pending"


# --- POST /api/crawl/all (alias) ---------------------------------------------


def test_crawl_all_requires_auth():
    client, _SessionLocal = _client()
    response = client.post("/api/crawl/all")
    assert response.status_code == 401


def test_crawl_all_enqueues_all_sources(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, _SessionLocal = _client()

    response = client.post("/api/crawl/all", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    from app.crawl_jobs import SOURCE_EXECUTORS

    assert {job["source"] for job in payload["created"]} == set(SOURCE_EXECUTORS.keys())
    # marked_off_market is no longer part of the synchronous response.
    assert "marked_off_market" not in payload
    assert "runs" not in payload


def test_crawl_all_triggers_off_market_refresh_in_background_pass(monkeypatch):
    """The off-market refresh used to run synchronously inside /crawl/all and
    be reported as marked_off_market in the response. It's now folded into
    process_backend_jobs, which the BackgroundTask runs after the response;
    we assert the effect (a stale listing gets off_market_at set) rather than
    a field in the HTTP response.
    """
    from datetime import datetime, timedelta

    from app.models import Listing, ListingSource

    _noop_backend_crawlers(monkeypatch)
    client, SessionLocal = _client()

    with SessionLocal() as db:
        listing = Listing(title="Stale villa", city="Frejus", price_eur=300000, living_area_m2=90)
        db.add(listing)
        db.flush()
        db.add(
            ListingSource(
                listing_id=listing.id,
                source="green-acres",
                source_id="ga-stale",
                url="http://x/1",
                last_seen_at=datetime.utcnow() - timedelta(hours=200),
            )
        )
        db.commit()
        listing_id = listing.id

    response = client.post("/api/crawl/all", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200

    with SessionLocal() as db:
        for _ in range(20):
            db.expire_all()
            refreshed = db.get(Listing, listing_id)
            if refreshed.off_market_at is not None:
                break
            time.sleep(0.05)
        assert refreshed.off_market_at is not None


# --- GET /api/crawl/jobs ------------------------------------------------------


def test_list_crawl_jobs_requires_secret_not_bearer(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        token = create_token(user)

    response = client.get("/api/crawl/jobs", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_list_crawl_jobs_no_auth():
    client, _SessionLocal = _client()
    response = client.get("/api/crawl/jobs")
    assert response.status_code == 401


def test_list_crawl_jobs_defaults_to_openclaw_pending(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, _SessionLocal = _client()
    client.post("/api/crawl/request", headers={"X-Crawl-Secret": "test-secret"})

    response = client.get("/api/crawl/jobs", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    assert payload  # at least seloger/pap/etc pending
    assert all(job["executor"] == "openclaw" for job in payload)
    assert all(job["status"] == "pending" for job in payload)


def test_list_crawl_jobs_filters_by_executor_and_status(monkeypatch):
    _noop_backend_crawlers(monkeypatch)
    client, _SessionLocal = _client()
    client.post("/api/crawl/request", headers={"X-Crawl-Secret": "test-secret"})

    response = client.get(
        "/api/crawl/jobs",
        params={"executor": "backend", "status": "pending"},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert response.status_code == 200
    payload = response.json()
    # backend jobs get processed by the BackgroundTask soon after, but right
    # after the synchronous enqueue call they may still show up as pending or
    # already done -- assert only on the executor/status contract, filtering.
    assert all(job["executor"] == "backend" for job in payload)


# --- POST /api/crawl/jobs/{id}/claim -----------------------------------------


def test_claim_job_endpoint_succeeds():
    client, SessionLocal = _client()
    response = client.post(
        "/api/crawl/request", json={"sources": ["seloger"]}, headers={"X-Crawl-Secret": "test-secret"}
    )
    job_id = response.json()["created"][0]["id"]

    claim_response = client.post(f"/api/crawl/jobs/{job_id}/claim", headers={"X-Crawl-Secret": "test-secret"})
    assert claim_response.status_code == 200
    assert claim_response.json()["status"] == "running"


def test_claim_job_endpoint_409_on_double_claim():
    client, _SessionLocal = _client()
    response = client.post(
        "/api/crawl/request", json={"sources": ["seloger"]}, headers={"X-Crawl-Secret": "test-secret"}
    )
    job_id = response.json()["created"][0]["id"]

    first = client.post(f"/api/crawl/jobs/{job_id}/claim", headers={"X-Crawl-Secret": "test-secret"})
    assert first.status_code == 200
    second = client.post(f"/api/crawl/jobs/{job_id}/claim", headers={"X-Crawl-Secret": "test-secret"})
    assert second.status_code == 409


def test_claim_job_endpoint_requires_secret():
    client, _SessionLocal = _client()
    response = client.post("/api/crawl/jobs/1/claim")
    assert response.status_code == 401


# --- POST /api/crawl/jobs/{id}/report ----------------------------------------


def test_report_job_endpoint_done():
    client, _SessionLocal = _client()
    response = client.post(
        "/api/crawl/request", json={"sources": ["seloger"]}, headers={"X-Crawl-Secret": "test-secret"}
    )
    job_id = response.json()["created"][0]["id"]
    client.post(f"/api/crawl/jobs/{job_id}/claim", headers={"X-Crawl-Secret": "test-secret"})

    report_response = client.post(
        f"/api/crawl/jobs/{job_id}/report",
        json={"status": "done", "found_count": 12},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert report_response.status_code == 200
    payload = report_response.json()
    assert payload["status"] == "done"
    assert payload["found_count"] == 12


def test_report_job_endpoint_error_with_message():
    client, _SessionLocal = _client()
    response = client.post(
        "/api/crawl/request", json={"sources": ["seloger"]}, headers={"X-Crawl-Secret": "test-secret"}
    )
    job_id = response.json()["created"][0]["id"]
    client.post(f"/api/crawl/jobs/{job_id}/claim", headers={"X-Crawl-Secret": "test-secret"})

    report_response = client.post(
        f"/api/crawl/jobs/{job_id}/report",
        json={"status": "error", "error": "browser timeout"},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert report_response.status_code == 200
    assert report_response.json()["error"] == "browser timeout"


def test_report_job_endpoint_409_if_not_running():
    client, _SessionLocal = _client()
    response = client.post(
        "/api/crawl/request", json={"sources": ["seloger"]}, headers={"X-Crawl-Secret": "test-secret"}
    )
    job_id = response.json()["created"][0]["id"]
    # never claimed -- still pending

    report_response = client.post(
        f"/api/crawl/jobs/{job_id}/report",
        json={"status": "done", "found_count": 1},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert report_response.status_code == 409


def test_report_job_endpoint_422_invalid_status():
    client, _SessionLocal = _client()
    response = client.post(
        "/api/crawl/request", json={"sources": ["seloger"]}, headers={"X-Crawl-Secret": "test-secret"}
    )
    job_id = response.json()["created"][0]["id"]
    client.post(f"/api/crawl/jobs/{job_id}/claim", headers={"X-Crawl-Secret": "test-secret"})

    report_response = client.post(
        f"/api/crawl/jobs/{job_id}/report",
        json={"status": "not-a-real-status"},
        headers={"X-Crawl-Secret": "test-secret"},
    )
    assert report_response.status_code == 422
