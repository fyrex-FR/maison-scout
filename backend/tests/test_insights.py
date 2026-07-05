"""Unit tests for app.insights (pure logic, no DB / no FastAPI)."""

from types import SimpleNamespace

import pytest

from app.insights import auto_flags, price_insight


def make_listing(**overrides):
    """Build a fake listing with sane, "clean" defaults, no photos by default.

    Individual tests override just the field(s) they care about.
    """

    defaults = dict(
        title="Belle maison",
        city="Frejus",
        price_eur=300_000,
        living_area_m2=100,
        land_area_m2=500,
        rooms=5,
        bedrooms=3,
        energy_rating="C",
        photos=[SimpleNamespace(url="http://example.com/1.jpg")],
        description="Une jolie maison.",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# auto_flags -- individual flags in isolation
# ---------------------------------------------------------------------------


def test_clean_listing_has_no_flags():
    listing = make_listing()
    assert auto_flags(listing) == []


@pytest.mark.parametrize("rating", ["F", "G"])
def test_dpe_poor_flag(rating):
    listing = make_listing(energy_rating=rating)
    flags = auto_flags(listing)
    assert {"code": "dpe_poor", "label": "DPE énergivore (F ou G)", "severity": "warn"} in flags


@pytest.mark.parametrize("rating", ["A", "B", "C", "D", "E", None])
def test_dpe_not_flagged_outside_f_g(rating):
    listing = make_listing(energy_rating=rating)
    codes = [f["code"] for f in auto_flags(listing)]
    assert "dpe_poor" not in codes


@pytest.mark.parametrize("photos", [[], None])
def test_no_photos_flag(photos):
    listing = make_listing(photos=photos)
    flags = auto_flags(listing)
    assert {"code": "no_photos", "label": "Aucune photo", "severity": "info"} in flags


def test_missing_photos_attribute_is_handled_defensively():
    listing = SimpleNamespace(
        title="x",
        city="y",
        price_eur=100_000,
        living_area_m2=50,
        land_area_m2=200,
        energy_rating="C",
        # no `.photos` attribute at all
    )
    flags = auto_flags(listing)
    codes = [f["code"] for f in flags]
    assert "no_photos" in codes


@pytest.mark.parametrize("area", [None, 0])
def test_no_living_area_flag(area):
    listing = make_listing(living_area_m2=area)
    flags = auto_flags(listing)
    assert {
        "code": "no_living_area",
        "label": "Surface habitable non renseignée",
        "severity": "warn",
    } in flags


@pytest.mark.parametrize("price", [None, 0])
def test_no_price_flag(price):
    listing = make_listing(price_eur=price)
    flags = auto_flags(listing)
    assert {"code": "no_price", "label": "Prix non communiqué", "severity": "warn"} in flags


@pytest.mark.parametrize("land", [None, 0])
def test_no_land_flag(land):
    listing = make_listing(land_area_m2=land)
    flags = auto_flags(listing)
    assert {"code": "no_land", "label": "Terrain non renseigné", "severity": "info"} in flags


def test_empty_listing_returns_multiple_flags_without_raising():
    listing = SimpleNamespace()
    flags = auto_flags(listing)
    codes = {f["code"] for f in flags}
    assert codes == {"no_photos", "no_living_area", "no_price", "no_land"}


# ---------------------------------------------------------------------------
# auto_flags -- ordering: warn before info
# ---------------------------------------------------------------------------


def test_warn_flags_come_before_info_flags():
    # Triggers both warn flags (dpe_poor, no_price) and info flags
    # (no_photos, no_land) simultaneously.
    listing = make_listing(
        energy_rating="G",
        price_eur=None,
        photos=[],
        land_area_m2=None,
    )
    flags = auto_flags(listing)
    severities = [f["severity"] for f in flags]
    # All warns must precede all infos.
    first_info_index = severities.index("info") if "info" in severities else len(severities)
    assert all(s == "warn" for s in severities[:first_info_index])
    assert all(s == "info" for s in severities[first_info_index:])
    assert "warn" in severities and "info" in severities


# ---------------------------------------------------------------------------
# auto_flags -- price/m2 thresholds vs. city median
# ---------------------------------------------------------------------------


def test_price_high_flag_above_1_4x_median():
    # median 3000 EUR/m2, listing at 100m2 / 450000 EUR => 4500 EUR/m2 (> 1.4x)
    listing = make_listing(price_eur=450_000, living_area_m2=100)
    flags = auto_flags(listing, city_median_price_per_m2=3000)
    assert {"code": "price_high", "label": "Prix/m² élevé pour la ville", "severity": "warn"} in flags


def test_price_low_flag_below_0_6x_median():
    # median 3000 EUR/m2, listing at 100m2 / 150000 EUR => 1500 EUR/m2 (< 0.6x)
    listing = make_listing(price_eur=150_000, living_area_m2=100)
    flags = auto_flags(listing, city_median_price_per_m2=3000)
    assert {
        "code": "price_low",
        "label": "Prix/m² étonnamment bas (à vérifier)",
        "severity": "info",
    } in flags


def test_price_within_normal_range_no_flag():
    # median 3000 EUR/m2, listing at 100m2 / 300000 EUR => 3000 EUR/m2 (== median)
    listing = make_listing(price_eur=300_000, living_area_m2=100)
    flags = auto_flags(listing, city_median_price_per_m2=3000)
    codes = [f["code"] for f in flags]
    assert "price_high" not in codes
    assert "price_low" not in codes


def test_no_price_flag_computed_without_median():
    # Without a median supplied, price_high/price_low must never appear,
    # regardless of how extreme the price is.
    listing = make_listing(price_eur=10_000_000, living_area_m2=10)
    flags = auto_flags(listing, city_median_price_per_m2=None)
    codes = [f["code"] for f in flags]
    assert "price_high" not in codes
    assert "price_low" not in codes


def test_price_threshold_not_triggered_when_price_or_area_missing():
    listing = make_listing(price_eur=None, living_area_m2=100)
    flags = auto_flags(listing, city_median_price_per_m2=3000)
    codes = [f["code"] for f in flags]
    assert "price_high" not in codes
    assert "price_low" not in codes


# ---------------------------------------------------------------------------
# price_insight
# ---------------------------------------------------------------------------


def test_price_insight_empty_everything():
    result = price_insight([], None)
    assert result == {
        "count": 0,
        "first_price": None,
        "last_price": None,
        "min_price": None,
        "max_price": None,
        "dropped": False,
        "change_abs": None,
        "change_ratio": None,
    }


def test_price_insight_single_point_from_current_price_only():
    result = price_insight([], 200_000)
    assert result["count"] == 1
    assert result["first_price"] == 200_000
    assert result["last_price"] == 200_000
    assert result["min_price"] == 200_000
    assert result["max_price"] == 200_000
    assert result["dropped"] is False
    assert result["change_abs"] is None
    assert result["change_ratio"] is None


def test_price_insight_single_historical_point_no_current():
    result = price_insight([250_000], None)
    assert result["count"] == 1
    assert result["first_price"] == 250_000
    assert result["last_price"] == 250_000
    assert result["change_abs"] is None
    assert result["change_ratio"] is None


def test_price_insight_descending_series_is_dropped():
    result = price_insight([300_000, 280_000], 260_000)
    assert result["count"] == 3
    assert result["first_price"] == 300_000
    assert result["last_price"] == 260_000
    assert result["min_price"] == 260_000
    assert result["max_price"] == 300_000
    assert result["dropped"] is True
    assert result["change_abs"] == -40_000
    assert result["change_ratio"] == round(-40_000 / 300_000, 4)


def test_price_insight_ascending_series_is_not_dropped():
    result = price_insight([200_000, 210_000], 230_000)
    assert result["count"] == 3
    assert result["first_price"] == 200_000
    assert result["last_price"] == 230_000
    assert result["min_price"] == 200_000
    assert result["max_price"] == 230_000
    assert result["dropped"] is False
    assert result["change_abs"] == 30_000
    assert result["change_ratio"] == round(30_000 / 200_000, 4)


def test_price_insight_current_price_equal_to_last_historical_not_duplicated():
    result = price_insight([300_000, 280_000], 280_000)
    # current_price == last historical price -> must not be appended again.
    assert result["count"] == 2
    assert result["first_price"] == 300_000
    assert result["last_price"] == 280_000
    assert result["dropped"] is True
    assert result["change_abs"] == -20_000


def test_price_insight_change_ratio_rounded_to_4_decimals():
    result = price_insight([300_000], 310_000)
    expected_ratio = round((310_000 - 300_000) / 300_000, 4)
    assert result["change_ratio"] == expected_ratio


def test_price_insight_first_price_zero_gives_none_ratio():
    result = price_insight([0], 100)
    assert result["change_abs"] == 100
    assert result["change_ratio"] is None


def test_price_insight_flat_series_not_dropped_and_zero_change():
    result = price_insight([300_000], 300_000)
    # current == last historical -> no duplication, only 1 point overall.
    assert result["count"] == 1
    assert result["dropped"] is False
    assert result["change_abs"] is None
    assert result["change_ratio"] is None


def test_price_insight_dropped_then_recovered_still_flags_drop_only_if_last_below_earlier():
    # last point (300k) is not below any earlier point, even though it dipped
    # in the middle -> dropped should be False per the "last < any earlier" rule.
    result = price_insight([300_000, 250_000], 300_000)
    assert result["count"] == 3
    assert result["last_price"] == 300_000
    assert result["dropped"] is False
    assert result["change_abs"] == 0
    assert result["change_ratio"] == 0.0
