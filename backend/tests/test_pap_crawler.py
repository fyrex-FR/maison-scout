from pathlib import Path

from app.crawlers.pap import PapCrawler

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_pap_parses_house_listing_from_html_fixture():
    html_content = (FIXTURES_DIR / "pap_frejus_search.html").read_text(encoding="utf-8")
    crawler = PapCrawler()

    listings = crawler._parse(html_content)

    # Only the house should survive (the apartment and the land plot must be
    # filtered out).
    assert len(listings) == 1
    listing = listings[0]

    assert listing.source == "pap"
    assert listing.source_id == "7654321"
    assert listing.title == "Maison 5 pieces 120 m2"
    assert listing.city == "Frejus"
    assert listing.postal_code == "83600"
    assert listing.price_eur == 450000
    assert listing.living_area_m2 == 120
    assert listing.land_area_m2 == 650
    assert listing.rooms == 5
    assert listing.bedrooms == 3
    assert listing.url == "https://www.pap.fr/annonce/vente-maison-frejus-83600-r7654321.html"
    assert len(listing.photos) == 2
    assert listing.photos[0] == "https://images.pap.fr/photos/7654321/1.jpg"
    assert listing.photos[1] == "https://images.pap.fr/photos/7654321/2.jpg"


def test_pap_filters_out_apartment_and_land():
    html_content = (FIXTURES_DIR / "pap_frejus_search.html").read_text(encoding="utf-8")
    crawler = PapCrawler()

    listings = crawler._parse(html_content)
    source_ids = {listing.source_id for listing in listings}

    assert "7654322" not in source_ids  # apartment excluded
    assert "7654323" not in source_ids  # land plot excluded


def test_pap_deduplicates_by_source_id():
    html_content = (FIXTURES_DIR / "pap_frejus_search.html").read_text(encoding="utf-8")
    # Simulate the same house card appearing twice (e.g. across paginated
    # search results merged together before parsing).
    duplicated = html_content.replace("</ul>\n</div>", "</ul>\n</div>", 1)
    crawler = PapCrawler()

    listings = crawler._parse(html_content + duplicated)

    house_ids = [listing.source_id for listing in listings if listing.source_id == "7654321"]
    assert len(house_ids) == 1


def test_pap_from_cities_defaults_to_target_cities_when_empty():
    crawler = PapCrawler.from_cities([])

    assert crawler.cities == sorted({"Frejus", "Saint-Raphael"})


def test_pap_from_cities_uses_provided_cities():
    crawler = PapCrawler.from_cities(["Frejus"])

    assert crawler.cities == ["Frejus"]


def test_pap_search_url_uses_known_postal_code():
    crawler = PapCrawler()

    assert crawler._search_url("Frejus") == "https://www.pap.fr/annonce/vente-maison-frejus-83600"
    assert crawler._search_url("Saint-Raphael") == "https://www.pap.fr/annonce/vente-maison-saint-raphael-83700"
