import pytest

from app.cities import CITY_METADATA, canonical_city_name, city_slug


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
        ("Aulnay sous bois", "Aulnay Sous Bois"),
        ("Aulnay-sous-bois", "Aulnay Sous Bois"),
        ("Aulnay-Sous-Bois", "Aulnay Sous Bois"),
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


def test_city_metadata_is_keyed_by_canonical_city_name():
    for city in CITY_METADATA:
        assert canonical_city_name(city) == city


def test_city_metadata_known_cities_have_postal_code_and_department():
    frejus = CITY_METADATA["Frejus"]
    assert frejus["postal_code"] == "83600"
    assert frejus["seloger_department"] == "83"

    saint_raphael = CITY_METADATA["Saint-Raphael"]
    assert saint_raphael["postal_code"] == "83700"
