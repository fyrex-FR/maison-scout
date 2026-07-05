from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

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

SECRET = {"X-Crawl-Secret": "test-secret"}


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


def _frejus_profile(db: Session, user: User, **criteria) -> SearchProfile:
    profile = SearchProfile(user_id=user.id, name="Frejus", city="Frejus", **criteria)
    db.add(profile)
    db.flush()
    return profile


def _listing(db: Session, source_id: str = "ga-1", **overrides) -> Listing:
    fields = dict(
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
    fields.update(overrides)
    listing = Listing(**fields)
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source="green-acres", source_id=source_id, url=f"https://ga/{source_id}"))
    db.add(ListingPhoto(listing_id=listing.id, url=f"https://cdn/{source_id}.jpg", position=0))
    db.flush()
    return listing


def _analysis(db: Session, listing: Listing) -> ListingAIAnalysis:
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
    db.add(analysis)
    db.flush()
    return analysis


# --- Endpoint A: pending-parse -------------------------------------------------


def test_pending_parse_requires_crawl_secret():
    client, _SessionLocal = _client()
    assert client.get("/api/ai/natural-search-profiles/pending-parse").status_code == 401


def test_pending_parse_returns_only_active_unparsed_profiles():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        active_unparsed = NaturalSearchProfile(
            user_id=user.id, name="A parser", raw_prompt="Piscine", is_active=True, parsed_at=None
        )
        already_parsed = NaturalSearchProfile(
            user_id=user.id, name="Deja parse", raw_prompt="Jardin", is_active=True, parsed_at=datetime.utcnow()
        )
        inactive = NaturalSearchProfile(
            user_id=user.id, name="Inactif", raw_prompt="Garage", is_active=False, parsed_at=None
        )
        db.add_all([active_unparsed, already_parsed, inactive])
        db.commit()
        active_unparsed_id = active_unparsed.id

    response = client.get("/api/ai/natural-search-profiles/pending-parse", headers=SECRET)
    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [active_unparsed_id]


def test_pending_parse_honours_limit_bounds():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        for i in range(3):
            db.add(
                NaturalSearchProfile(
                    user_id=user.id, name=f"P{i}", raw_prompt=f"prompt {i}", is_active=True, parsed_at=None
                )
            )
        db.commit()

    response = client.get("/api/ai/natural-search-profiles/pending-parse?limit=2", headers=SECRET)
    assert response.status_code == 200
    assert len(response.json()) == 2

    # limit is clamped to >= 1
    response = client.get("/api/ai/natural-search-profiles/pending-parse?limit=0", headers=SECRET)
    assert response.status_code == 200
    assert len(response.json()) == 1


# --- Endpoint B: pending-match-scores -----------------------------------------


def test_pending_match_scores_requires_crawl_secret():
    client, _SessionLocal = _client()
    assert client.get("/api/ai/pending-match-scores").status_code == 401


def test_pending_match_scores_returns_pair_when_unscored():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _frejus_profile(db, user, min_bedrooms=4)
        profile = NaturalSearchProfile(
            user_id=user.id, name="Actif parse", raw_prompt="Piscine", is_active=True, parsed_at=datetime.utcnow()
        )
        db.add(profile)
        listing = _listing(db, "ga-scored")
        analysis = _analysis(db, listing)
        db.commit()
        listing_id = listing.id
        profile_id = profile.id
        analysis_id = analysis.id

    response = client.get("/api/ai/pending-match-scores", headers=SECRET)
    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {"listing_id": listing_id, "natural_search_profile_id": profile_id, "source_analysis_id": analysis_id}
    ]


def test_pending_match_scores_excludes_up_to_date_scores():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _frejus_profile(db, user)
        profile = NaturalSearchProfile(
            user_id=user.id, name="Actif parse", raw_prompt="Piscine", is_active=True, parsed_at=datetime.utcnow()
        )
        db.add(profile)
        listing = _listing(db, "ga-uptodate")
        analysis = _analysis(db, listing)
        db.flush()
        db.add(
            ListingMatchScore(
                listing_id=listing.id,
                natural_search_profile_id=profile.id,
                score=80,
                source_analysis_id=analysis.id,  # same analysis -> up to date
            )
        )
        db.commit()

    response = client.get("/api/ai/pending-match-scores", headers=SECRET)
    assert response.status_code == 200
    assert response.json() == []


def test_pending_match_scores_returns_pair_when_score_is_stale():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _frejus_profile(db, user)
        profile = NaturalSearchProfile(
            user_id=user.id, name="Actif parse", raw_prompt="Piscine", is_active=True, parsed_at=datetime.utcnow()
        )
        db.add(profile)
        listing = _listing(db, "ga-stale")
        analysis = _analysis(db, listing)
        db.flush()
        db.add(
            ListingMatchScore(
                listing_id=listing.id,
                natural_search_profile_id=profile.id,
                score=80,
                source_analysis_id=analysis.id + 999,  # points at an older/other analysis -> stale
            )
        )
        db.commit()
        listing_id = listing.id
        profile_id = profile.id
        analysis_id = analysis.id

    response = client.get("/api/ai/pending-match-scores", headers=SECRET)
    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {"listing_id": listing_id, "natural_search_profile_id": profile_id, "source_analysis_id": analysis_id}
    ]


def test_pending_match_scores_excludes_listing_outside_classic_criteria():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        # Classic profile demands >= 4 bedrooms.
        _frejus_profile(db, user, min_bedrooms=4)
        profile = NaturalSearchProfile(
            user_id=user.id, name="Actif parse", raw_prompt="Piscine", is_active=True, parsed_at=datetime.utcnow()
        )
        db.add(profile)
        # Listing with a KNOWN too-low bedroom count violates the criterion.
        listing = _listing(db, "ga-toofew", bedrooms=2)
        _analysis(db, listing)
        db.commit()

    response = client.get("/api/ai/pending-match-scores", headers=SECRET)
    assert response.status_code == 200
    assert response.json() == []


def test_pending_match_scores_ignores_inactive_or_unparsed_profiles_and_unanalyzed_listings():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _frejus_profile(db, user)
        # inactive parsed profile -> ignored
        db.add(
            NaturalSearchProfile(
                user_id=user.id, name="Inactif", raw_prompt="x", is_active=False, parsed_at=datetime.utcnow()
            )
        )
        # active but unparsed profile -> ignored
        db.add(
            NaturalSearchProfile(
                user_id=user.id, name="Non parse", raw_prompt="y", is_active=True, parsed_at=None
            )
        )
        # a listing with no analysis -> never a candidate
        _listing(db, "ga-noanalysis")
        db.commit()

    response = client.get("/api/ai/pending-match-scores", headers=SECRET)
    assert response.status_code == 200
    assert response.json() == []
