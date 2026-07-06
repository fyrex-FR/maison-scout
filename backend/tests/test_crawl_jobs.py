"""Unit tests for app.crawl_jobs (enqueue / claim / report / zombie expiry).

Pure data-layer tests against an in-memory SQLite session -- no network, no
FastAPI TestClient here (see test_crawl_jobs_api.py for the HTTP-level
behaviour of /api/crawl/request, /api/crawl/all and /api/crawl/jobs*).
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawl_jobs import SOURCE_EXECUTORS, claim_job, enqueue_jobs, report_job
from app.db import Base
from app.models import CrawlJob


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# --- enqueue_jobs -----------------------------------------------------------


def test_enqueue_all_sources_creates_one_job_per_known_source(db):
    result = enqueue_jobs(db, None, requested_by="x@example.com")

    created_sources = {job.source for job in result["created"]}
    assert created_sources == set(SOURCE_EXECUTORS.keys())
    assert result["skipped"] == []
    assert result["unknown"] == []
    for job in result["created"]:
        assert job.status == "pending"
        assert job.executor == SOURCE_EXECUTORS[job.source]
        assert job.requested_by == "x@example.com"


def test_enqueue_specific_sources_only(db):
    result = enqueue_jobs(db, ["green-acres", "seloger"], requested_by="crawl-secret")
    created_sources = {job.source for job in result["created"]}
    assert created_sources == {"green-acres", "seloger"}


def test_enqueue_unknown_source_is_listed_and_ignored(db):
    result = enqueue_jobs(db, ["green-acres", "not-a-real-source"], requested_by="crawl-secret")
    created_sources = {job.source for job in result["created"]}
    assert created_sources == {"green-acres"}
    assert result["unknown"] == ["not-a-real-source"]


def test_enqueue_skips_source_with_existing_pending_job(db):
    first = enqueue_jobs(db, ["green-acres"], requested_by="a")
    assert len(first["created"]) == 1

    second = enqueue_jobs(db, ["green-acres"], requested_by="b")
    assert second["created"] == []
    assert second["skipped"] == ["green-acres"]


def test_enqueue_skips_source_with_existing_running_job(db):
    job = CrawlJob(source="bien-ici", executor="backend", status="running", claimed_at=datetime.utcnow())
    db.add(job)
    db.commit()

    result = enqueue_jobs(db, ["bien-ici"], requested_by="a")
    assert result["created"] == []
    assert result["skipped"] == ["bien-ici"]


def test_enqueue_requested_by_is_recorded(db):
    result = enqueue_jobs(db, ["pap"], requested_by="someone@example.com")
    assert result["created"][0].requested_by == "someone@example.com"


# --- claim_job ---------------------------------------------------------------


def test_claim_pending_job_succeeds(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id

    claimed = claim_job(db, job_id)
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.claimed_at is not None


def test_claim_already_running_job_fails(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id
    assert claim_job(db, job_id) is not None

    second_claim = claim_job(db, job_id)
    assert second_claim is None


def test_claim_done_job_fails(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id
    claim_job(db, job_id)
    report_job(db, job_id, status="done", found_count=5)

    assert claim_job(db, job_id) is None


def test_claim_nonexistent_job_returns_none(db):
    assert claim_job(db, 999999) is None


# --- report_job ---------------------------------------------------------------


def test_report_done_with_found_count(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id
    claim_job(db, job_id)

    job = report_job(db, job_id, status="done", found_count=42, crawl_run_id=7)
    assert job is not None
    assert job.status == "done"
    assert job.found_count == 42
    assert job.crawl_run_id == 7
    assert job.finished_at is not None


def test_report_error_with_message(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id
    claim_job(db, job_id)

    job = report_job(db, job_id, status="error", error="boom")
    assert job is not None
    assert job.status == "error"
    assert job.error == "boom"


def test_report_job_not_running_returns_none(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id
    # still pending, never claimed
    assert report_job(db, job_id, status="done", found_count=1) is None


def test_report_job_already_done_returns_none(db):
    result = enqueue_jobs(db, ["green-acres"], requested_by="a")
    job_id = result["created"][0].id
    claim_job(db, job_id)
    report_job(db, job_id, status="done", found_count=1)

    assert report_job(db, job_id, status="done", found_count=2) is None


# --- zombie jobs ---------------------------------------------------------------


def test_zombie_running_job_expires_to_error_and_unblocks_source(db):
    stale_job = CrawlJob(
        source="green-acres",
        executor="backend",
        status="running",
        claimed_at=datetime.utcnow() - timedelta(hours=3),
    )
    db.add(stale_job)
    db.commit()
    stale_id = stale_job.id

    result = enqueue_jobs(db, ["green-acres"], requested_by="a")

    db.refresh(stale_job)
    assert stale_job.status == "error"
    assert stale_job.error == "stale: exécuteur silencieux depuis plus de 2h"
    assert stale_job.finished_at is not None

    # The zombie no longer blocks a fresh enqueue for the same source.
    created_sources = {job.source for job in result["created"]}
    assert "green-acres" in created_sources
    new_job = next(job for job in result["created"] if job.source == "green-acres")
    assert new_job.id != stale_id


def test_running_job_within_2h_is_not_expired(db):
    fresh_job = CrawlJob(
        source="bien-ici",
        executor="backend",
        status="running",
        claimed_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(fresh_job)
    db.commit()

    result = enqueue_jobs(db, ["bien-ici"], requested_by="a")

    db.refresh(fresh_job)
    assert fresh_job.status == "running"
    assert result["created"] == []
    assert result["skipped"] == ["bien-ici"]
