from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.db import Base, get_db
from app.main import app, ai_listing_source_hash
from app.models import (
    Listing,
    ListingAIAnalysis,
    ListingMatchScore,
    ListingPhoto,
    ListingSource,
    NaturalSearchProfile,
    SearchProfile,
    User,
)


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    settings.crawl_secret = "test-secret"
    client = TestClient(app)
    return client, SessionLocal


def _user(db: Session, email: str = "x@example.com") -> User:
    user = User(email=email, display_name=email.split("@")[0], password_hash="hash")
    db.add(user)
    db.flush()
    return user


def _listing(db: Session, source_id: str = "ga-1") -> Listing:
    listing = Listing(
        title="Villa avec piscine",
        city="Frejus",
        postal_code="83600",
        price_eur=750000,
        living_area_m2=150,
        land_area_m2=800,
        bedrooms=4,
        description="Maison avec séjour ouvert sur terrasse et piscine.",
        status="new",
    )
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id=source_id, url=f"https://ga/{source_id}"))
    db.add(ListingPhoto(listing_id=listing.id, url=f"https://cdn/{source_id}.jpg", position=0))
    db.commit()
    db.refresh(listing)
    return listing


def test_user_can_manage_only_their_own_natural_search_profiles():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db, "x@example.com")
        other = _user(db, "other@example.com")
        db.commit()
        token = create_token(user)
        other_profile = NaturalSearchProfile(user_id=other.id, name="Other", raw_prompt="Grand terrain")
        db.add(other_profile)
        db.commit()
        other_profile_id = other_profile.id

    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/natural-search-profiles",
        headers=headers,
        json={"name": "Maison familiale", "raw_prompt": "4 chambres, piscine, acces jardin sans escalier"},
    )
    assert response.status_code == 200
    created = response.json()
    assert created["raw_prompt"] == "4 chambres, piscine, acces jardin sans escalier"
    assert created["criteria_json"] == {}

    response = client.get("/api/natural-search-profiles", headers=headers)
    assert response.status_code == 200
    assert [profile["id"] for profile in response.json()] == [created["id"]]

    response = client.patch(
        f"/api/natural-search-profiles/{other_profile_id}",
        headers=headers,
        json={"raw_prompt": "Tentative"},
    )
    assert response.status_code == 404


def test_ai_analysis_queue_and_writeback_require_crawl_secret():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        listing = _listing(db)
        listing_id = listing.id
        expected_hash = ai_listing_source_hash(listing)

    assert client.get("/api/ai/listings/pending-analysis").status_code == 401

    response = client.get("/api/ai/listings/pending-analysis", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == listing_id
    assert payload[0]["source_hash"] == expected_hash

    response = client.put(
        f"/api/ai/listings/{listing_id}/analysis",
        headers={"X-Crawl-Secret": "test-secret"},
        json={
            "summary": "Maison familiale avec piscine.",
            "features_json": {"pool": "yes", "bedrooms": 4},
            "red_flags_json": [],
            "confidence_json": {"pool": 0.95},
            "photo_observations_json": [{"url": "https://cdn/1.jpg", "observation": "Piscine visible"}],
            "source_hash": expected_hash,
            "model": "gpt-test",
        },
    )
    assert response.status_code == 200
    assert response.json()["features_json"]["pool"] == "yes"

    response = client.get("/api/ai/listings/pending-analysis", headers={"X-Crawl-Secret": "test-secret"})
    assert response.status_code == 200
    assert response.json() == []

    with SessionLocal() as db:
        analysis = db.scalar(select(ListingAIAnalysis).where(ListingAIAnalysis.listing_id == listing_id))
        assert analysis.source_hash == expected_hash


def test_ai_worker_can_store_profile_parse_and_match_score():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        listing = _listing(db)
        profile = NaturalSearchProfile(user_id=user.id, name="Famille", raw_prompt="Piscine et 4 chambres")
        analysis = ListingAIAnalysis(
            listing_id=listing.id,
            summary="Maison avec piscine",
            features_json={"pool": "yes"},
            red_flags_json=[],
            confidence_json={},
            photo_observations_json=[],
            source_hash=ai_listing_source_hash(listing),
            model="gpt-test",
        )
        db.add_all([profile, analysis])
        db.commit()
        listing_id = listing.id
        profile_id = profile.id
        analysis_id = analysis.id

    response = client.put(
        f"/api/ai/natural-search-profiles/{profile_id}/parse",
        headers={"X-Crawl-Secret": "test-secret"},
        json={"criteria_json": {"pool": "required"}, "weights_json": {"pool": 30}, "parsed_model": "gpt-test"},
    )
    assert response.status_code == 200
    assert response.json()["criteria_json"]["pool"] == "required"

    response = client.put(
        "/api/ai/match-scores",
        headers={"X-Crawl-Secret": "test-secret"},
        json={
            "listing_id": listing_id,
            "natural_search_profile_id": profile_id,
            "score": 91,
            "matched_reasons_json": ["Piscine visible", "4 chambres"],
            "missing_or_uncertain_json": ["Clim non confirmee"],
            "dealbreakers_json": [],
            "model": "gpt-test",
            "source_analysis_id": analysis_id,
        },
    )
    assert response.status_code == 200
    assert response.json()["score"] == 91

    with SessionLocal() as db:
        stored = db.scalar(select(ListingMatchScore))
        assert stored.listing_id == listing_id
        assert stored.natural_search_profile_id == profile_id
        assert stored.matched_reasons_json == ["Piscine visible", "4 chambres"]


def test_listing_endpoint_applies_standard_search_profile_criteria():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        db.add(
            SearchProfile(
                user_id=user.id,
                name="Frejus filtre",
                city="Frejus",
                max_price_eur=800000,
                min_living_area_m2=120,
                min_land_area_m2=500,
                min_bedrooms=4,
            )
        )
        matching = _listing(db, "ga-match")
        too_expensive = _listing(db, "ga-price")
        too_expensive.price_eur = 900000
        too_small = _listing(db, "ga-small")
        too_small.living_area_m2 = 90
        other_city = _listing(db, "ga-other")
        other_city.city = "Saint-Raphael"
        db.commit()
        matching_id = matching.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert [listing["id"] for listing in response.json()] == [matching_id]
