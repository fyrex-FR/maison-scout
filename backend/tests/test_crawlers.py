import json
from pathlib import Path

from app.crawlers.bien_ici import BienIciCrawler
from app.crawlers.green_acres import GreenAcresCrawler

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_green_acres_parses_house_listing_from_html_fixture():
    html_content = (FIXTURES_DIR / "green_acres_listing.html").read_text(encoding="utf-8")
    crawler = GreenAcresCrawler()

    listings = crawler._parse(html_content)

    # Only the house should survive (the T3 apartment card must be filtered out).
    assert len(listings) == 1
    listing = listings[0]

    assert listing.source == "green-acres"
    assert listing.source_id == "12345"
    assert listing.title == "Belle villa avec piscine et vue mer"
    assert listing.city == "Frejus"  # accented "Fréjus" normalized via canonical_city_name
    assert listing.postal_code == "83600"
    assert listing.price_eur == 450000
    assert listing.living_area_m2 == 120
    assert listing.land_area_m2 == 650
    assert listing.rooms == 5
    assert listing.bedrooms == 3
    assert len(listing.photos) == 2
    assert listing.photos[0] == "https://cdn.green-acres.fr/photos/12345-1.jpg"


def test_bien_ici_parses_house_ad_from_json_fixture():
    data = json.loads((FIXTURES_DIR / "bien_ici_response.json").read_text(encoding="utf-8"))
    crawler = BienIciCrawler()

    house_ads = [ad for ad in data["realEstateAds"] if crawler._is_house_ad(ad)]
    assert len(house_ads) == 1

    listing = crawler._parse_ad(house_ads[0])

    assert listing.source == "bien-ici"
    assert listing.source_id == "555111"
    assert listing.title == "Villa contemporaine avec piscine"
    assert listing.city == "Saint-Raphael"  # accented "Saint-Raphaël" normalized
    assert listing.postal_code == "83700"
    assert listing.price_eur == 620000
    assert listing.living_area_m2 == 145
    assert listing.land_area_m2 == 800
    assert listing.bedrooms == 4
    assert listing.energy_rating == "C"
    assert len(listing.photos) == 2


def test_bien_ici_filters_out_non_house_ads():
    data = json.loads((FIXTURES_DIR / "bien_ici_response.json").read_text(encoding="utf-8"))
    crawler = BienIciCrawler()

    flat_ad = data["realEstateAds"][1]
    assert crawler._is_house_ad(flat_ad) is False
