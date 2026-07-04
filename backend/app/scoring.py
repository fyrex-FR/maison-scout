from app.models import Listing


def score_listing(listing: Listing) -> int:
    score = 50

    if listing.bedrooms and listing.bedrooms >= 3:
        score += 10
    if listing.living_area_m2 and listing.living_area_m2 >= 100:
        score += 10
    if listing.land_area_m2 and listing.land_area_m2 >= 400:
        score += 10
    if listing.energy_rating in {"A", "B", "C"}:
        score += 5
    if listing.energy_rating in {"F", "G"}:
        score -= 10

    return max(0, min(100, score))

