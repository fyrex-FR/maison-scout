import pytest

from app.cities import canonical_city_name, city_slug


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Frejus", "Frejus"),
        ("Fréjus", "Frejus"),
        ("FREJUS", "Frejus"),
        ("frejus", "Frejus"),
        ("  Fréjus  ", "Frejus"),
        ("Saint-Raphael", "Saint-Raphael"),
        ("Saint-Raphaël", "Saint-Raphael"),
        ("St-Raphaël", "Saint-Raphael"),
        ("St Raphael", "Saint-Raphael"),
        ("saint raphael", "Saint-Raphael"),
        ("SAINT RAPHAEL", "Saint-Raphael"),
    ],
)
def test_known_aliases_resolve_to_canonical_name(raw, expected):
    assert canonical_city_name(raw) == expected


def test_unknown_city_is_normalized_deterministically():
    result = canonical_city_name("sainte-maxime")
    assert result == "Sainte-Maxime"
    # Same normalization applied twice must be stable / idempotent.
    assert canonical_city_name(result) == result


def test_unknown_city_never_raises_and_strips_accents():
    result = canonical_city_name("Cannet-des-Maures")
    assert "é" not in result.lower()
    assert result == "Cannet-Des-Maures"


def test_unknown_city_with_mixed_whitespace_and_case():
    assert canonical_city_name("  LE   MUY ") == "Le Muy"


def test_empty_city_returns_empty_string():
    assert canonical_city_name("") == ""
    assert canonical_city_name("   ") == ""


def test_city_slug_uses_canonical_name():
    assert city_slug("Fréjus") == "frejus"
    assert city_slug("St-Raphaël") == "saint-raphael"
    assert city_slug("Sainte-Maxime") == "sainte-maxime"


def test_city_slug_has_no_accents_or_uppercase():
    slug = city_slug("Cannes-La-Bocca")
    assert slug == slug.lower()
    assert all(char.isalnum() or char == "-" for char in slug)
