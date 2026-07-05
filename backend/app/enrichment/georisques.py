"""Georisques (French natural/technological risk registry) enrichment.

Georisques exposes a free, no-key-required API that, given coordinates,
reports whether a location is exposed to each of a fixed list of natural and
technological risks (flood, clay shrink-swell, wildfire, earthquake, ...).
This is deterministic open data (no AI), hence it lives in the backend next
to the crawlers, mirroring app.enrichment.dvf.

Split the same way as dvf.py:
- PURE: `summarize_risks` turns the API's verbose JSON payload into the
  compact dict stored on `Listing.georisques_json` and shown in the UI.
- I/O: `fetch_risks` calls the API for one point; `enrich_listings_risks`
  batches this across listings that need a (re)check.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Listing

__all__ = ["summarize_risks", "fetch_risks", "enrich_listings_risks"]

GEORISQUES_URL = "https://www.georisques.gouv.fr/api/v1/resultats_rapport_risque"

# Maps our compact output key -> Georisques payload key under "risquesNaturels"
# (Georisques uses camelCase; our compact dict uses short French-oriented
# snake_case keys matching the UI labels).
_NATURAL_RISK_KEYS = {
    "inondation": "inondation",
    "argiles": "retraitGonflementArgile",
    "feu_foret": "feuForet",
    "seisme": "seisme",
    "radon": "radon",
    "risque_cotier": "risqueCotier",
    "mouvement_terrain": "mouvementTerrain",
}
# pollution_sols lives under "risquesTechnologiques" in the Georisques payload.
_TECHNOLOGICAL_RISK_KEYS = {
    "pollution_sols": "pollutionSols",
}

_STALE_AFTER = timedelta(days=30)
_HTTP_TIMEOUT = 30.0
_POLITE_DELAY_SECONDS = 0.5


def _present(section: dict | None, key: str) -> bool:
    if not section:
        return False
    risk = section.get(key)
    if not isinstance(risk, dict):
        return False
    return bool(risk.get("present"))


def summarize_risks(payload: dict) -> dict:
    """Reduce a Georisques API payload to the compact dict the app stores/shows.

    `payload` is expected to have `risquesNaturels` / `risquesTechnologiques`
    dict keys, each mapping a risk name to an object with a `present: bool`
    field. Any missing key, missing section, or malformed entry is treated as
    "not present" (False) rather than raising -- Georisques's response shape
    is not something we control, and a partial/odd payload should degrade to
    "no risk detected" rather than crash the enrichment run.

    Returns a dict with exactly these keys (all bool): `inondation`,
    `argiles`, `feu_foret`, `seisme`, `radon`, `risque_cotier`,
    `mouvement_terrain`, `pollution_sols`.
    """

    payload = payload or {}
    naturels = payload.get("risquesNaturels")
    technologiques = payload.get("risquesTechnologiques")

    summary = {
        key: _present(naturels, source_key) for key, source_key in _NATURAL_RISK_KEYS.items()
    }
    summary.update(
        {key: _present(technologiques, source_key) for key, source_key in _TECHNOLOGICAL_RISK_KEYS.items()}
    )
    return summary


async def fetch_risks(client: httpx.AsyncClient, latitude: float, longitude: float) -> dict | None:
    """Fetch and summarize Georisques risks for one point, or None on failure.

    Georisques's `latlon` query parameter takes longitude FIRST, then
    latitude (`latlon={lon},{lat}`) -- the opposite order of the more common
    lat,lon convention, easy to get backwards.

    Any HTTP error, timeout, or unparseable response returns None so a single
    listing's failure never breaks a batch enrichment run.
    """

    try:
        response = await client.get(
            GEORISQUES_URL,
            params={"latlon": f"{longitude},{latitude}"},
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return summarize_risks(payload)


async def enrich_listings_risks(db: Session, *, limit: int = 40) -> dict:
    """(Re)check Georisques risks for listings with coordinates that need it.

    Candidates: listings with non-null latitude/longitude AND
    (`georisques_checked_at` is NULL OR older than 30 days), capped at
    `limit` per run to stay polite towards the free public API. A short
    `asyncio.sleep(0.5)` between calls is a courtesy delay, not a strict rate
    limit enforced by Georisques.

    A single listing's failure (network error, bad payload) is recorded as
    "failed" and does not stop the run or roll back other listings' results.

    Returns `{"checked": n, "failed": n}`. Commits after each successful
    listing so partial progress survives even if a later listing errors.
    """

    import asyncio

    now = datetime.utcnow()
    cutoff = now - _STALE_AFTER

    stmt = (
        select(Listing)
        .where(
            Listing.latitude.is_not(None),
            Listing.longitude.is_not(None),
            or_(Listing.georisques_checked_at.is_(None), Listing.georisques_checked_at < cutoff),
        )
        .limit(limit)
    )
    listings = list(db.scalars(stmt).all())

    counts = {"checked": 0, "failed": 0}
    if not listings:
        return counts

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for index, listing in enumerate(listings):
            try:
                summary = await fetch_risks(client, listing.latitude, listing.longitude)
                if summary is None:
                    counts["failed"] += 1
                else:
                    listing.georisques_json = summary
                    listing.georisques_checked_at = now
                    db.commit()
                    counts["checked"] += 1
            except Exception:
                db.rollback()
                counts["failed"] += 1

            if index < len(listings) - 1:
                await asyncio.sleep(_POLITE_DELAY_SECONDS)

    return counts
