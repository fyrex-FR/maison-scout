"""Listing lifecycle helpers: detecting and un-detecting "off market" listings.

A listing is considered off the market once none of its `listing_sources`
rows have been re-seen by a crawl/ingest within `settings.off_market_after_hours`.
We don't delete anything: `listings.off_market_at` is set to the detection
timestamp (NULL means "still active"). This lets the UI keep a favorited /
to-call listing visible with a "retirée" badge instead of having it silently
vanish, while still hiding stale noise from the default list view.

Detection (`refresh_off_market_status`) is deliberately batched (a constant
number of queries regardless of how many listings exist) so it's cheap to run
after every crawl. Resurrection (a listing coming back after being marked off
market) is handled separately in `app.ingest.upsert_listing`, since that's the
only place a source's `last_seen_at` is bumped.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Listing, ListingSource

__all__ = ["refresh_off_market_status"]


def refresh_off_market_status(db: Session, *, now: datetime | None = None) -> dict:
    """Mark listings off-market when all of their sources have gone stale.

    A listing is marked off market when it is currently active
    (`off_market_at IS NULL`) and the most recent `last_seen_at` across ALL of
    its `listing_sources` is older than `now - off_market_after_hours`. A
    listing with no sources at all is left untouched (nothing to judge
    staleness from).

    Runs in a constant number of queries: one aggregate query to get the most
    recent `last_seen_at` per listing_id, then a single bulk UPDATE for the
    listings that qualify -- never one query per listing.

    Returns `{"marked_off_market": <int>}`, the number of listings updated.
    Does not commit; the caller is responsible for `db.commit()`.
    """

    now = now or datetime.utcnow()
    cutoff = now - timedelta(hours=settings.off_market_after_hours)

    latest_seen_by_listing_id = dict(
        db.execute(
            select(ListingSource.listing_id, func.max(ListingSource.last_seen_at)).group_by(
                ListingSource.listing_id
            )
        ).all()
    )
    if not latest_seen_by_listing_id:
        return {"marked_off_market": 0}

    stale_listing_ids = [
        listing_id
        for listing_id, latest_seen_at in latest_seen_by_listing_id.items()
        if latest_seen_at is not None and latest_seen_at < cutoff
    ]
    if not stale_listing_ids:
        return {"marked_off_market": 0}

    active_stale_ids = list(
        db.scalars(
            select(Listing.id).where(
                Listing.id.in_(stale_listing_ids),
                Listing.off_market_at.is_(None),
            )
        ).all()
    )
    if not active_stale_ids:
        return {"marked_off_market": 0}

    for listing in db.scalars(select(Listing).where(Listing.id.in_(active_stale_ids))).all():
        listing.off_market_at = now

    return {"marked_off_market": len(active_stale_ids)}
