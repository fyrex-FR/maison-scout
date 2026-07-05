"""DVF (Demandes de Valeurs Foncieres, Etalab open data) enrichment.

DVF publishes every real-estate *sale* recorded by French notaries, per
commune, as a yearly CSV. Unlike a listing's asking price, DVF gives us what
houses actually sold for -- a much stronger signal to judge whether a
listing's asking price/m2 is reasonable. This module is entirely
deterministic (no AI, no API key) and lives in the backend next to the
crawlers for that reason.

Two areas, kept strictly separate so the parsing logic can be unit tested
without any network access:
- PURE: `median_house_price_per_m2` parses already-downloaded CSV text.
- I/O: `fetch_commune_csv` downloads one commune/year CSV over HTTP;
  `refresh_city_stats` orchestrates city -> INSEE resolution, download,
  parsing and upserting `CityMarketStat` rows.

DVF parsing rule (deliberately conservative -- see module docstring in the
task brief for the full rationale): a `id_mutation` groups every row of a
single real-estate transaction; `valeur_fonciere` is the mutation's TOTAL
price repeated on every row, not a per-lot price. We only trust a mutation
as "one house's price" when, among its rows with a non-empty `type_local`,
there is EXACTLY one "Maison" row (a "Dependance" row alongside it, with no
living area of its own, is fine -- garages/cellars sold with the house don't
invalidate the price). Price/m2 = valeur_fonciere / surface_reelle_bati of
that Maison row. We then discard obvious data-entry aberrations outside
[500, 20000] EUR/m2 and take the median of what's left.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cities import canonical_city_name
from app.models import CityMarketStat

__all__ = [
    "median_house_price_per_m2",
    "fetch_commune_csv",
    "refresh_city_stats",
]

DVF_URL_TEMPLATE = "https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{dept}/{insee}.csv"
GEO_API_URL = "https://geo.api.gouv.fr/communes"

# Known INSEE codes for the app's seed cities (app.cities.canonical_city_name
# canonical spellings). Any other city falls back to the geo.api.gouv.fr
# lookup in `resolve_insee_code`.
_KNOWN_INSEE_CODES = {
    "Frejus": "83061",
    "Saint-Raphael": "83118",
}

_MIN_PRICE_PER_M2 = 500.0
_MAX_PRICE_PER_M2 = 20000.0
_STALE_AFTER = timedelta(days=30)
_HTTP_TIMEOUT = 30.0


def _is_missing(value: str | None) -> bool:
    return value is None or value.strip() == ""


def median_house_price_per_m2(csv_texts: list[str]) -> tuple[float | None, int]:
    """Compute the median real-sale price/m2 for houses from raw DVF CSV text(s).

    `csv_texts` is a list of full CSV file contents (e.g. one per year) using
    the DVF header (`id_mutation, ..., nature_mutation, valeur_fonciere, ...,
    type_local, surface_reelle_bati, ...`). Rows across all provided texts
    are pooled into one set of mutations before the per-mutation rule is
    applied (a mutation id is only unique within a single year's file).

    Returns `(median_price_per_m2, sample_count)`. `median_price_per_m2` is
    `None` when no mutation survives the filtering rule (e.g. empty input).
    """

    # id_mutation is only unique per source file, so key rows by
    # (file_index, id_mutation) to avoid accidentally merging unrelated
    # mutations that happen to share an id across different years.
    rows_by_mutation: dict[tuple[int, str], list[dict]] = defaultdict(list)

    for file_index, csv_text in enumerate(csv_texts):
        if not csv_text:
            continue
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            mutation_id = row.get("id_mutation")
            if _is_missing(mutation_id):
                continue
            rows_by_mutation[(file_index, mutation_id)].append(row)

    prices_per_m2: list[float] = []

    for rows in rows_by_mutation.values():
        nature = rows[0].get("nature_mutation")
        if (nature or "").strip() != "Vente":
            continue

        rows_with_type = [row for row in rows if not _is_missing(row.get("type_local"))]
        house_rows = [row for row in rows_with_type if (row.get("type_local") or "").strip() == "Maison"]
        if len(house_rows) != 1:
            continue
        # Any other typed row alongside the house must be a mere dependance
        # (garage, cave, ...); a second dwelling in the same mutation makes
        # the total price ambiguous per-lot, so we bail out.
        other_rows = [row for row in rows_with_type if row is not house_rows[0]]
        if any((row.get("type_local") or "").strip() != "Dependance" for row in other_rows):
            continue

        house_row = house_rows[0]
        valeur_fonciere_raw = house_row.get("valeur_fonciere")
        surface_raw = house_row.get("surface_reelle_bati")
        if _is_missing(valeur_fonciere_raw) or _is_missing(surface_raw):
            continue
        try:
            valeur_fonciere = float(valeur_fonciere_raw.replace(",", "."))
            surface = float(surface_raw.replace(",", "."))
        except (ValueError, AttributeError):
            continue
        if valeur_fonciere <= 0 or surface <= 0:
            continue

        price_per_m2 = valeur_fonciere / surface
        if not (_MIN_PRICE_PER_M2 <= price_per_m2 <= _MAX_PRICE_PER_M2):
            continue

        prices_per_m2.append(price_per_m2)

    if not prices_per_m2:
        return None, 0
    return median(prices_per_m2), len(prices_per_m2)


async def fetch_commune_csv(client: httpx.AsyncClient, year: int, insee: str) -> str | None:
    """Download one commune's DVF CSV for `year`, or None if unavailable.

    The dataset is served from an OVH S3 bucket via a 302 redirect, so the
    caller's client MUST be built with `follow_redirects=True`. A 404 (year
    not yet published for this commune, or invalid INSEE code) or any
    network error is treated as "no data for this year" rather than raised,
    since the caller tries several years and combines whichever succeed.
    """

    dept = insee[:2]
    url = DVF_URL_TEMPLATE.format(year=year, dept=dept, insee=insee)
    try:
        response = await client.get(url, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    return response.text


async def resolve_insee_code(client: httpx.AsyncClient, city: str) -> str | None:
    """Resolve a canonical city name to an INSEE commune code.

    Checks the hardcoded seed first (fast, no network, covers the app's known
    target cities), then falls back to the geo.api.gouv.fr commune search for
    any other city. Never raises: any lookup failure returns None so the
    caller can skip that city without crashing the whole refresh.
    """

    canonical = canonical_city_name(city)
    if canonical in _KNOWN_INSEE_CODES:
        return _KNOWN_INSEE_CODES[canonical]

    try:
        response = await client.get(
            GEO_API_URL,
            params={"nom": canonical, "fields": "code", "boost": "population", "limit": 1},
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not results:
        return None
    return results[0].get("code")


async def refresh_city_stats(db: Session, cities: list[str]) -> dict:
    """Refresh `CityMarketStat` rows for the given cities (DVF median price/m2).

    For each canonical city: skip if a fresh (`computed_at` within the last
    30 days) row already exists; otherwise resolve its INSEE code, download
    the last few years of DVF CSVs (current year, then year-1, year-2 --
    keeping the first two that respond 200, since the current year is
    usually not published yet), compute the median house price/m2, and
    upsert the row (even when the result is "no data", so we don't retry a
    commune with no DVF houses on every single run).

    Returns `{"refreshed": n, "skipped": n, "failed": n}`. A single city's
    failure (INSEE resolution failure, no CSV available, network error)
    never aborts the others.
    """

    counts = {"refreshed": 0, "skipped": 0, "failed": 0}
    now = datetime.utcnow()
    current_year = now.year

    canonical_cities = sorted({canonical_city_name(city) for city in cities if city and city.strip()})

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for city in canonical_cities:
            existing = db.scalar(select(CityMarketStat).where(CityMarketStat.city == city))
            if existing is not None and existing.computed_at is not None and now - existing.computed_at < _STALE_AFTER:
                counts["skipped"] += 1
                continue

            try:
                insee = await resolve_insee_code(client, city)
                if not insee:
                    counts["failed"] += 1
                    continue

                csv_texts: list[str] = []
                years_used: list[int] = []
                for year in (current_year, current_year - 1, current_year - 2):
                    if len(csv_texts) >= 2:
                        break
                    csv_text = await fetch_commune_csv(client, year, insee)
                    if csv_text is not None:
                        csv_texts.append(csv_text)
                        years_used.append(year)

                if not csv_texts:
                    counts["failed"] += 1
                    continue

                median_price, sample_count = median_house_price_per_m2(csv_texts)
                years_used.sort()
                period_label = f"DVF {years_used[0]}–{years_used[-1]}" if len(years_used) > 1 else f"DVF {years_used[0]}"

                if existing is None:
                    existing = CityMarketStat(city=city)
                    db.add(existing)
                existing.insee_code = insee
                existing.median_price_per_m2_house = median_price
                existing.sample_count = sample_count
                existing.period_label = period_label
                existing.computed_at = now
                db.commit()
                counts["refreshed"] += 1
            except Exception:
                db.rollback()
                counts["failed"] += 1

    return counts
