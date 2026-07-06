from pathlib import Path

from app.crawlers.paruvendu import ParuVenduCrawler

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture() -> str:
    return (FIXTURES_DIR / "paruvendu_frejus_search.html").read_text(encoding="utf-8")


def test_paruvendu_parses_house_listings_from_html_fixture():
    crawler = ParuVenduCrawler()

    listings = crawler._parse(_load_fixture(), city="Frejus", postal_code="83600")

    # The fixture is a trimmed excerpt with 3 cards (out of ~119 on a real page).
    assert len(listings) == 3

    first = listings[0]
    assert first.source == "paruvendu"
    assert first.source_id == "1291835860A1KIVHMN000"
    assert first.url == "https://www.paruvendu.fr/immobilier/vente/maison/1291835860A1KIVHMN000"
    assert first.title == "Maison - 2 pièce(s) - 36 m²"
    assert first.city == "Frejus"
    assert first.postal_code == "83600"
    assert first.price_eur == 185000
    assert first.living_area_m2 == 36
    assert len(first.photos) == 2


def test_paruvendu_tolerates_missing_optional_fields():
    """The first card has no "chambres"/"terrain" badges at all -- the
    crawler must not raise and must simply report None for those fields.
    """
    crawler = ParuVenduCrawler()

    listings = crawler._parse(_load_fixture(), city="Frejus", postal_code="83600")
    first = listings[0]

    assert first.rooms == 2
    assert first.bedrooms is None
    assert first.land_area_m2 is None


def test_paruvendu_extracts_full_fields_when_present():
    crawler = ParuVenduCrawler()

    listings = crawler._parse(_load_fixture(), city="Frejus", postal_code="83600")
    second = listings[1]

    assert second.source_id == "1292373162A1KIVHMN000"
    assert second.price_eur == 450000
    assert second.living_area_m2 == 78
    assert second.land_area_m2 == 40
    assert second.rooms == 4
    assert second.bedrooms == 3
    assert second.description is not None


def test_paruvendu_handles_villa_url_variant():
    crawler = ParuVenduCrawler()

    listings = crawler._parse(_load_fixture(), city="Frejus", postal_code="83600")
    villa = listings[2]

    assert villa.source_id == "1288851133A1KIVHVI000"
    assert villa.url == "https://www.paruvendu.fr/immobilier/vente/villa/1288851133A1KIVHVI000"
    assert villa.title.startswith("Villa")


def test_paruvendu_deduplicates_by_source_id():
    crawler = ParuVenduCrawler()
    content = _load_fixture()

    listings = crawler._parse(content + content, city="Frejus", postal_code="83600")
    source_ids = [listing.source_id for listing in listings]

    assert len(source_ids) == len(set(source_ids)) == 3


def test_paruvendu_from_cities_defaults_to_target_cities_when_empty():
    crawler = ParuVenduCrawler.from_cities([])

    assert crawler.cities == sorted({"Frejus", "Saint-Raphael"})


def test_paruvendu_from_cities_uses_provided_cities():
    crawler = ParuVenduCrawler.from_cities(["Frejus"])

    assert crawler.cities == ["Frejus"]


def test_paruvendu_search_urls_use_known_postal_code_and_pagination():
    crawler = ParuVenduCrawler()

    urls = crawler._search_urls("Frejus")

    assert urls[0] == "https://www.paruvendu.fr/immobilier/vente/maison/frejus-83600/"
    assert urls[1] == "https://www.paruvendu.fr/immobilier/vente/maison/frejus-83600/?p=2"
    assert len(urls) == 3  # capped at MAX_PAGES_PER_CITY


def test_paruvendu_ignores_city_without_metadata():
    """A followed city with no entry in CITY_METADATA must be silently
    skipped (no postal code -> no valid search URL -> no error).
    """
    crawler = ParuVenduCrawler()

    urls = crawler._search_urls("Ville Inconnue Sans Metadata")

    assert urls == []


def test_paruvendu_has_next_page_detects_pagination_link():
    crawler = ParuVenduCrawler()

    assert crawler._has_next_page(_load_fixture()) is True
    assert crawler._has_next_page("<html><body>no next page here</body></html>") is False
