"""Pure-logic helpers that turn raw listing data into human-facing signals.

This module intentionally has NO dependency on the database, SQLAlchemy, or
FastAPI. Everything here is plain Python (stdlib only) so it can be unit
tested in isolation and reused by any layer (API routes, background jobs,
scripts, ...).

Public API
----------
- ``auto_flags(listing, *, city_median_price_per_m2=None, days_on_market=None) -> list[dict]``
- ``price_insight(prices_chronological, current_price) -> dict``

See each function's docstring for the exact contract.
"""

from __future__ import annotations

__all__ = ["auto_flags", "price_insight"]


def _is_missing_number(value: float | int | None) -> bool:
    """True when a numeric field should be treated as "not provided".

    Both ``None`` and ``0`` (or ``0.0``) count as missing for surface area /
    price fields: a real listing never legitimately has a living area or a
    price of exactly zero, so a stored ``0`` is effectively "unknown".
    """

    return value is None or value == 0


def auto_flags(
    listing,
    *,
    city_median_price_per_m2: float | None = None,
    days_on_market: int | None = None,
) -> list[dict]:
    """Compute deterministic data-quality / pricing signals for a listing.

    ``listing`` is expected to expose (at least, via attribute access) the
    same fields as the ``Listing`` ORM model: ``energy_rating``, ``price_eur``,
    ``living_area_m2``, ``land_area_m2`` and ``photos`` (a list-like of photo
    objects). Any missing/None attribute is treated defensively as "absent"
    rather than raising -- this function must never throw because of missing
    data, including when called with an essentially empty object.

    ``days_on_market``, when provided by the caller, is the number of days
    the listing has been on the market (only meaningful for still-active
    listings -- the caller should pass ``None`` for an off-market listing,
    since suggesting a negotiation lever on a withdrawn listing makes no
    sense). At 60+ days it surfaces an info-level "long_on_market" flag: a
    long time on the market is a soft signal that there may be room to
    negotiate.

    Returns a list of ``{"code": str, "label": str, "severity": "warn"|"info"}``
    dicts. "warn" entries are always ordered before "info" entries; the
    relative order within each severity group follows the rule evaluation
    order documented below.
    """

    warn_flags: list[dict] = []
    info_flags: list[dict] = []

    energy_rating = getattr(listing, "energy_rating", None)
    if energy_rating in ("F", "G"):
        warn_flags.append(
            {
                "code": "dpe_poor",
                "label": "DPE énergivore (F ou G)",
                "severity": "warn",
            }
        )

    photos = getattr(listing, "photos", None)
    if not photos:
        info_flags.append(
            {
                "code": "no_photos",
                "label": "Aucune photo",
                "severity": "info",
            }
        )

    living_area_m2 = getattr(listing, "living_area_m2", None)
    if _is_missing_number(living_area_m2):
        warn_flags.append(
            {
                "code": "no_living_area",
                "label": "Surface habitable non renseignée",
                "severity": "warn",
            }
        )

    price_eur = getattr(listing, "price_eur", None)
    if _is_missing_number(price_eur):
        warn_flags.append(
            {
                "code": "no_price",
                "label": "Prix non communiqué",
                "severity": "warn",
            }
        )

    land_area_m2 = getattr(listing, "land_area_m2", None)
    if _is_missing_number(land_area_m2):
        info_flags.append(
            {
                "code": "no_land",
                "label": "Terrain non renseigné",
                "severity": "info",
            }
        )

    if (
        city_median_price_per_m2 is not None
        and city_median_price_per_m2 > 0
        and not _is_missing_number(price_eur)
        and not _is_missing_number(living_area_m2)
    ):
        price_per_m2 = price_eur / living_area_m2
        if price_per_m2 > 1.4 * city_median_price_per_m2:
            warn_flags.append(
                {
                    "code": "price_high",
                    "label": "Prix/m² élevé pour la ville",
                    "severity": "warn",
                }
            )
        elif price_per_m2 < 0.6 * city_median_price_per_m2:
            info_flags.append(
                {
                    "code": "price_low",
                    "label": "Prix/m² étonnamment bas (à vérifier)",
                    "severity": "info",
                }
            )

    if days_on_market is not None and days_on_market >= 60:
        info_flags.append(
            {
                "code": "long_on_market",
                "label": "Sur le marché depuis plus de 2 mois",
                "severity": "info",
            }
        )

    return warn_flags + info_flags


def price_insight(prices_chronological: list[int], current_price: int | None) -> dict:
    """Summarize a listing's price history.

    ``prices_chronological`` holds previously observed prices, oldest first.
    ``current_price`` is the listing's present price (may be ``None``).

    The full series is built as ``prices_chronological + [current_price]``,
    unless the last historical price already equals ``current_price`` -- in
    that case ``current_price`` is not appended again, to avoid a spurious
    duplicate data point.

    Returns a dict with exactly the keys documented in the module's public
    contract (see class docstring / task spec): ``count``, ``first_price``,
    ``last_price``, ``min_price``, ``max_price``, ``dropped``, ``change_abs``,
    ``change_ratio``.
    """

    series: list[int] = [p for p in prices_chronological if p is not None]

    if current_price is not None and (not series or series[-1] != current_price):
        series.append(current_price)

    if not series:
        return {
            "count": 0,
            "first_price": None,
            "last_price": None,
            "min_price": None,
            "max_price": None,
            "dropped": False,
            "change_abs": None,
            "change_ratio": None,
        }

    first_price = series[0]
    last_price = series[-1]
    min_price = min(series)
    max_price = max(series)

    dropped = any(last_price < earlier for earlier in series[:-1])

    if len(series) < 2:
        change_abs = None
        change_ratio = None
    else:
        change_abs = last_price - first_price
        change_ratio = round((last_price - first_price) / first_price, 4) if first_price != 0 else None

    return {
        "count": len(series),
        "first_price": first_price,
        "last_price": last_price,
        "min_price": min_price,
        "max_price": max_price,
        "dropped": dropped,
        "change_abs": change_abs,
        "change_ratio": change_ratio,
    }
