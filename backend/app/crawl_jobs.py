"""Crawl job queue: the backend's control plane for crawl execution.

Today crawl execution is split across two brains that don't talk to each
other: synchronous in-backend endpoints for httpx-friendly sources, and blind
OpenClaw crons for browser-only sources (Cloudflare/DataDome-protected). That
means the backend has no visibility into whether the external browser worker
is even running, and the external worker has no idea what's already fresh.

This module inverts that: the backend decides *what* needs crawling (one
`CrawlJob` row per source per request) and owns the single source→executor
registry. *Where* the work actually runs stays split on purpose --
"backend" jobs are cheap enough to run in-process (see
`process_backend_jobs`), "openclaw" jobs require a real browser and can't be
pushed to from here, so the external worker pulls its own jobs via the
crawl-secret-protected endpoints in app/main.py (claim -> do the work ->
report), the standard pattern for a worker the control plane can't reach
directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.crawlers.bien_ici import BienIciCrawler
from app.crawlers.green_acres import GreenAcresCrawler
from app.crawlers.paruvendu import ParuVenduCrawler
from app.ingest import run_crawler
from app.lifecycle import refresh_off_market_status
from app.models import CrawlJob

__all__ = [
    "SOURCE_EXECUTORS",
    "enqueue_jobs",
    "claim_job",
    "report_job",
    "process_backend_jobs",
]

# Single source of truth for "who runs this source". httpx-friendly sources
# stay in-process ("backend"); sources behind Cloudflare/DataDome or that
# otherwise need a real browser are pulled by the external OpenClaw worker.
# ParuVendu was validated in prod (191 listings ingested via httpx) so it
# rejoins the normal in-process rotation here.
SOURCE_EXECUTORS: dict[str, str] = {
    "green-acres": "backend",
    "bien-ici": "backend",
    "paruvendu": "backend",
    "seloger": "openclaw",
    "logic-immo": "openclaw",
    "notaires": "openclaw",
    "pap": "openclaw",
    # LeBonCoin uses the community `lbc` client from the external
    # Maison Scout LeBonCoin worker, not the browser-based OpenClaw scraper.
    "leboncoin": "leboncoin",
}

# A job stuck in "running" past this age is assumed to belong to a dead
# executor (crashed worker, killed process, ...) rather than one still in
# progress -- otherwise a single silent failure would block that source's
# queue forever, since enqueue_jobs treats an active job as "already covered".
_STALE_RUNNING_AFTER = timedelta(hours=2)
_STALE_ERROR_MESSAGE = "stale: exécuteur silencieux depuis plus de 2h"

# Factory per backend-executed source, built lazily inside process_backend_jobs
# so this module has no import-time dependency on request-scoped state (e.g.
# the list of active cities, which requires a db session).
_BACKEND_CRAWLER_FACTORIES = {
    "green-acres": GreenAcresCrawler.from_cities,
    "bien-ici": BienIciCrawler.from_cities,
    "paruvendu": ParuVenduCrawler.from_cities,
}


def _expire_stale_running_jobs(db: Session, *, now: datetime | None = None) -> int:
    """Flip long-running jobs to "error" so they stop blocking their source.

    Returns the number of jobs expired. Does not commit -- callers that need
    the expiry to be durable before proceeding (enqueue_jobs) commit
    themselves right after calling this.
    """
    now = now or datetime.utcnow()
    cutoff = now - _STALE_RUNNING_AFTER
    stale_ids = list(
        db.scalars(
            select(CrawlJob.id).where(CrawlJob.status == "running", CrawlJob.claimed_at < cutoff)
        ).all()
    )
    if not stale_ids:
        return 0
    for job in db.scalars(select(CrawlJob).where(CrawlJob.id.in_(stale_ids))).all():
        job.status = "error"
        job.error = _STALE_ERROR_MESSAGE
        job.finished_at = now
    return len(stale_ids)


def enqueue_jobs(db: Session, sources: list[str] | None, requested_by: str) -> dict:
    """Create pending CrawlJob rows for the requested sources.

    `sources=None` means "all known sources" (SOURCE_EXECUTORS keys).
    Guards against duplicate work: a source with an already-active
    (pending/running) job is skipped rather than double-queued. Zombie jobs
    (running for more than 2h) are expired to "error" first so a dead
    executor can never permanently block its source.

    Returns {"created": [...], "skipped": [...], "unknown": [...]}.
    """
    _expire_stale_running_jobs(db)
    db.commit()

    requested = sources if sources is not None else list(SOURCE_EXECUTORS.keys())

    known: list[str] = []
    unknown: list[str] = []
    for source in requested:
        (known if source in SOURCE_EXECUTORS else unknown).append(source)

    active_sources = set(
        db.scalars(
            select(CrawlJob.source).where(
                CrawlJob.source.in_(known),
                CrawlJob.status.in_(["pending", "running"]),
            )
        ).all()
    )

    created: list[CrawlJob] = []
    skipped: list[str] = []
    for source in known:
        if source in active_sources:
            skipped.append(source)
            continue
        job = CrawlJob(
            source=source,
            executor=SOURCE_EXECUTORS[source],
            status="pending",
            requested_by=requested_by,
        )
        db.add(job)
        created.append(job)

    db.commit()
    for job in created:
        db.refresh(job)

    return {"created": created, "skipped": skipped, "unknown": unknown}


def claim_job(db: Session, job_id: int) -> CrawlJob | None:
    """Atomically transition a job pending -> running.

    Uses a conditional UPDATE (WHERE status='pending') so two concurrent
    claimers can never both "win" the same job -- only one UPDATE can match
    the row, the other affects zero rows and gets None back.
    """
    now = datetime.utcnow()
    result = db.execute(
        update(CrawlJob)
        .where(CrawlJob.id == job_id, CrawlJob.status == "pending")
        .values(status="running", claimed_at=now)
    )
    db.commit()
    if result.rowcount == 0:
        return None
    return db.get(CrawlJob, job_id)


def report_job(
    db: Session,
    job_id: int,
    status: str,
    found_count: int | None = None,
    error: str | None = None,
    crawl_run_id: int | None = None,
) -> CrawlJob | None:
    """Transition a running job to a terminal status ("done" or "error").

    Returns None if the job doesn't exist or isn't currently "running" (the
    caller -- an API endpoint -- turns that into a 409, since reporting on a
    job that was never claimed, or already reported, is a client error).
    """
    job = db.get(CrawlJob, job_id)
    if job is None or job.status != "running":
        return None
    job.status = status
    job.found_count = found_count
    job.error = error
    job.crawl_run_id = crawl_run_id
    job.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


def active_search_cities(db: Session) -> list[str]:
    """Local copy of app.main.active_search_cities to avoid importing main.py

    (main.py imports this module, so the reverse import would be circular).
    Kept trivial and re-derives from the same SearchProfile table.
    """
    from app.models import SearchProfile

    cities = list(db.scalars(select(SearchProfile.city).where(SearchProfile.enabled == True)).all())  # noqa: E712
    return sorted(set(cities))


async def process_backend_jobs(db: Session) -> list[dict]:
    """Claim and run every pending "backend"-executor job, in-process.

    Mirrors the pre-existing behaviour of POST /api/crawl/all: build each
    source's crawler from the active search cities, run it through the
    shared run_crawler pipeline, then refresh off-market status once at the
    end (not per-job) and commit -- exactly as /crawl/all did before this
    queue existed. Each job is reported (done/error) individually so
    GET /api/crawl/jobs reflects real per-source outcomes.
    """
    cities = active_search_cities(db)
    pending_ids = list(
        db.scalars(
            select(CrawlJob.id).where(CrawlJob.executor == "backend", CrawlJob.status == "pending")
        ).all()
    )

    results: list[dict] = []
    for job_id in pending_ids:
        job = claim_job(db, job_id)
        if job is None:
            continue  # already claimed/expired concurrently

        factory = _BACKEND_CRAWLER_FACTORIES.get(job.source)
        if factory is None:
            report_job(db, job.id, "error", found_count=0, error=f"no backend crawler for source {job.source!r}")
            results.append({"source": job.source, "status": "error", "found_count": 0})
            continue

        crawler = factory(cities)
        run = await run_crawler(db, crawler)
        # CrawlRun uses "ok"/"error"; CrawlJob uses "done"/"error" (see the
        # brief's status vocabulary) -- translate rather than leak CrawlRun's
        # vocabulary into the job queue.
        job_status = "done" if run.status == "ok" else "error"
        report_job(
            db,
            job.id,
            status=job_status,
            found_count=run.found_count,
            error=run.error,
            crawl_run_id=run.id,
        )
        results.append({"source": run.source, "status": job_status, "found_count": run.found_count})

    refresh_off_market_status(db)
    db.commit()
    return results
