from sqlalchemy import create_engine
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


def _allow_frejus(db: Session, user: User) -> None:
    """/api/listings only returns listings whose city matches an enabled
    SearchProfile for the user; add a permissive one so enrichment tests can
    focus on the AI/match fields without being filtered out by city rules.
    """
    db.add(SearchProfile(user_id=user.id, name="Frejus", city="Frejus"))
    db.commit()


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


def test_health_reports_ok_when_db_reachable():
    client, _SessionLocal = _client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_degraded_when_db_unreachable():
    """The dependency resolves to a session, but running a query against it
    fails (e.g. connection dropped) -- /health must catch that and report
    degraded with a 503 rather than bubbling up a 500.
    """
    client, _SessionLocal = _client()

    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db unreachable")

    def broken_get_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = broken_get_db
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


def test_api_listings_exposes_ai_summary_and_active_profile_match_score():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = _listing(db, "ga-enriched")
        active_profile = NaturalSearchProfile(
            user_id=user.id,
            name="Recherche principale",
            raw_prompt="Piscine et 4 chambres",
            is_active=True,
        )
        db.add(active_profile)
        db.flush()
        analysis = ListingAIAnalysis(
            listing_id=listing.id,
            summary="Belle maison avec piscine, bon etat general.",
            features_json={"pool": "yes"},
            red_flags_json=["Toiture a verifier"],
            confidence_json={"pool": 0.9},
            photo_observations_json=[],
            source_hash=ai_listing_source_hash(listing),
            model="gpt-test",
        )
        db.add(analysis)
        db.flush()
        match = ListingMatchScore(
            listing_id=listing.id,
            natural_search_profile_id=active_profile.id,
            score=87,
            matched_reasons_json=["Piscine visible", "4 chambres confirmees"],
            missing_or_uncertain_json=["Chauffage non precise"],
            dealbreakers_json=[],
            model="gpt-test",
            source_analysis_id=analysis.id,
        )
        db.add(match)
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == listing_id
    assert item["ai_summary"] == "Belle maison avec piscine, bon etat general."
    assert item["red_flags"] == ["Toiture a verifier"]
    assert item["match_score"] == 87
    assert item["match_reasons"] == ["Piscine visible", "4 chambres confirmees"]
    assert item["match_missing"] == ["Chauffage non precise"]
    assert item["match_dealbreakers"] == []
    assert item["active_profile_name"] == "Recherche principale"


def test_api_listings_defaults_ai_and_match_fields_when_no_analysis_exists():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = _listing(db, "ga-plain")
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == listing_id
    assert item["ai_summary"] is None
    assert item["red_flags"] == []
    assert item["match_score"] is None
    assert item["match_reasons"] == []
    assert item["match_missing"] == []
    assert item["match_dealbreakers"] == []
    assert item["active_profile_name"] is None


def test_api_listings_ignores_inactive_or_other_users_natural_profile_matches():
    """Match scores should only surface for the current user's active profile.

    A match tied to an inactive profile, or to another user's profile, must
    not leak into this user's listing payload.
    """
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db, "owner@example.com")
        _allow_frejus(db, user)
        other_user = _user(db, "other@example.com")
        listing = _listing(db, "ga-isolated")
        inactive_profile = NaturalSearchProfile(
            user_id=user.id,
            name="Ancienne recherche",
            raw_prompt="Ancien besoin",
            is_active=False,
        )
        other_users_profile = NaturalSearchProfile(
            user_id=other_user.id,
            name="Profil d'un autre utilisateur",
            raw_prompt="Autre besoin",
            is_active=True,
        )
        db.add_all([inactive_profile, other_users_profile])
        db.flush()
        db.add(
            ListingMatchScore(
                listing_id=listing.id,
                natural_search_profile_id=inactive_profile.id,
                score=42,
                matched_reasons_json=["Ne devrait pas apparaitre"],
            )
        )
        db.add(
            ListingMatchScore(
                listing_id=listing.id,
                natural_search_profile_id=other_users_profile.id,
                score=99,
                matched_reasons_json=["Ne devrait pas apparaitre non plus"],
            )
        )
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == listing_id
    assert item["match_score"] is None
    assert item["match_reasons"] == []
    assert item["active_profile_name"] is None


def test_api_listings_picks_most_recently_updated_active_profile_when_several():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        _allow_frejus(db, user)
        listing = _listing(db, "ga-multi-profile")
        older_active = NaturalSearchProfile(
            user_id=user.id,
            name="Ancien profil actif",
            raw_prompt="Premier besoin",
            is_active=True,
        )
        db.add(older_active)
        db.commit()

        newer_active = NaturalSearchProfile(
            user_id=user.id,
            name="Profil actif recent",
            raw_prompt="Besoin actuel",
            is_active=True,
        )
        db.add(newer_active)
        db.commit()

        db.add(
            ListingMatchScore(
                listing_id=listing.id,
                natural_search_profile_id=newer_active.id,
                score=73,
                matched_reasons_json=["Correspond au profil recent"],
            )
        )
        db.commit()
        listing_id = listing.id
        token = create_token(user)

    response = client.get("/api/listings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == listing_id
    assert item["active_profile_name"] == "Profil actif recent"
    assert item["match_score"] == 73
