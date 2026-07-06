"""API tests for GET /api/sources/status.

Exercises the freshness/next-expected-run contract that the frontend
consumes: per-source listings_count, last crawl_runs row summary, and the
overdue computation (next_expected_at = last started_at + crawl_interval_hours,
with a 45-minute grace period) -- see docs/PROJECT_CONTEXT.md for the wider
crawl/ingestion architecture this endpoint reports on.
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
from app.models import CrawlJob, CrawlRun, Listing, ListingSource, User


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


def _listing(db: Session, source: str, source_id: str) -> Listing:
    listing = Listing(
        title=f"Villa {source_id}",
        city="Frejus",
        postal_code="83600",
        price_eur=400000,
        living_area_m2=100,
        status="new",
    )
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source=source, source_id=source_id, url=f"https://x/{source_id}"))
    db.commit()
    return listing


def _run(
    db: Session,
    source: str,
    *,
    status: str = "ok",
    found_count: int = 0,
    error: str | None = None,
    started_at: datetime,
    finished_at: datetime | None = None,
) -> CrawlRun:
    run = CrawlRun(
        source=source,
        status=status,
        found_count=found_count,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_source_with_recent_ok_run_reports_correct_fields_and_not_overdue():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "green-acres", "ga-1")
        _listing(db, "green-acres", "ga-2")
        started = datetime.utcnow() - timedelta(hours=1)
        finished = started + timedelta(minutes=5)
        _run(db, "green-acres", status="ok", found_count=69, started_at=started, finished_at=finished)
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "green-acres")

    assert entry["listings_count"] == 2
    assert entry["last_status"] == "ok"
    assert entry["last_found_count"] == 69
    assert entry["last_error"] is None
    assert entry["overdue"] is False

    last_run_at = datetime.fromisoformat(entry["last_run_at"])
    next_expected_at = datetime.fromisoformat(entry["next_expected_at"])
    assert last_run_at.utcoffset() is not None
    assert next_expected_at.utcoffset() is not None
    assert next_expected_at == started.replace(tzinfo=last_run_at.tzinfo) + timedelta(hours=settings.crawl_interval_hours)


def test_source_with_stale_run_is_overdue():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "bien-ici", "bi-1")
        started = datetime.utcnow() - timedelta(hours=8)
        _run(db, "bien-ici", status="ok", found_count=10, started_at=started, finished_at=started + timedelta(minutes=3))
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "bien-ici")
    assert entry["overdue"] is True


def test_source_with_errored_last_run_reports_error_status():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "pap", "pap-1")
        older = datetime.utcnow() - timedelta(hours=2)
        newer = datetime.utcnow() - timedelta(minutes=10)
        _run(db, "pap", status="ok", found_count=5, started_at=older, finished_at=older + timedelta(minutes=2))
        _run(db, "pap", status="error", found_count=0, error="boom", started_at=newer, finished_at=None)
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "pap")
    assert entry["last_status"] == "error"
    assert entry["last_error"] == "boom"
    # finished_at is null on the errored run -> last_run_at falls back to started_at.
    # FastAPI/pydantic serializes aware UTC datetimes with a trailing "Z";
    # normalize it back to +00:00 before comparing against the naive fixture.
    assert entry["last_run_at"].replace("Z", "+00:00") == newer.isoformat() + "+00:00"


def test_source_present_only_in_listing_sources_has_null_run_fields():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "seloger", "sl-1")
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "seloger")

    assert entry["listings_count"] == 1
    assert entry["last_run_at"] is None
    assert entry["last_status"] is None
    assert entry["last_found_count"] is None
    assert entry["last_error"] is None
    assert entry["next_expected_at"] is None
    assert entry["overdue"] is False


def test_demo_source_is_excluded():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "demo", "demo-1")
        _run(db, "demo", status="ok", found_count=1, started_at=datetime.utcnow())
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert all(item["source"] != "demo" for item in payload)


def test_results_sorted_by_listings_count_descending():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "small-source", "s-1")
        for i in range(3):
            _listing(db, "big-source", f"b-{i}")
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    counts = [item["listings_count"] for item in payload]
    assert counts == sorted(counts, reverse=True)
    assert payload[0]["source"] == "big-source"


def test_sources_status_requires_authentication():
    client, _SessionLocal = _client()
    response = client.get("/api/sources/status")
    assert response.status_code == 401


def test_job_status_is_null_when_no_active_job(monkeypatch):
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "green-acres", "ga-1")
        _run(db, "green-acres", status="ok", found_count=1, started_at=datetime.utcnow())
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "green-acres")
    assert entry["job_status"] is None


def test_job_status_reports_pending_job(monkeypatch):
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "seloger", "sl-1")
        db.add(CrawlJob(source="seloger", executor="openclaw", status="pending"))
        db.commit()
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "seloger")
    assert entry["job_status"] == "pending"


def test_job_status_prioritizes_running_over_pending(monkeypatch):
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "pap", "pap-1")
        # Shouldn't normally coexist given the anti-duplicate guard, but the
        # endpoint should still prefer "running" deterministically if it does.
        db.add(CrawlJob(source="pap", executor="openclaw", status="pending"))
        db.add(CrawlJob(source="pap", executor="openclaw", status="running", claimed_at=datetime.utcnow()))
        db.commit()
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "pap")
    assert entry["job_status"] == "running"


def test_job_status_ignores_done_and_error_jobs(monkeypatch):
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "green-acres", "ga-1")
        db.add(CrawlJob(source="green-acres", executor="backend", status="done", found_count=3))
        db.commit()
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "green-acres")
    assert entry["job_status"] is None


def test_serialized_datetimes_include_utc_offset():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _listing(db, "green-acres", "ga-1")
        _run(db, "green-acres", status="ok", found_count=1, started_at=datetime.utcnow())
        token = create_token(user)

    response = client.get("/api/sources/status", headers={"Authorization": f"Bearer {token}"})
    payload = response.json()
    entry = next(item for item in payload if item["source"] == "green-acres")
    assert entry["last_run_at"].endswith("+00:00") or entry["last_run_at"].endswith("Z")
    assert entry["next_expected_at"].endswith("+00:00") or entry["next_expected_at"].endswith("Z")
