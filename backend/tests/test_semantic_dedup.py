from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ComparisonItem, Listing, ListingPhoto, ListingSource, SemanticDedupDecision, User, UserListingState
from app.semantic_dedup import merge_listings, reject_pair, semantic_candidate_pairs


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _listing(db: Session, *, source: str, source_id: str, title: str = "Villa", price: int = 500000) -> Listing:
    listing = Listing(
        title=title,
        city="Frejus",
        postal_code="83600",
        price_eur=price,
        living_area_m2=120,
        description=f"{title} avec piscine et jardin",
        status="new",
    )
    db.add(listing)
    db.flush()
    db.add(ListingSource(listing_id=listing.id, source=source, source_id=source_id, url=f"https://{source}/{source_id}"))
    db.add(ListingPhoto(listing_id=listing.id, url=f"https://cdn/{source_id}.jpg", position=0))
    db.flush()
    return listing


def test_semantic_candidate_pairs_returns_unreviewed_cross_source_pairs():
    db = next(_db())
    green = _listing(db, source="green-acres", source_id="ga-1")
    bien = _listing(db, source="bien-ici", source_id="bi-1")
    db.commit()

    pairs = semantic_candidate_pairs(db, days=30)

    assert [(left.id, right.id) for left, right in pairs] == [(bien.id, green.id)]


def test_rejected_pair_is_not_returned_again():
    db = next(_db())
    green = _listing(db, source="green-acres", source_id="ga-1")
    bien = _listing(db, source="bien-ici", source_id="bi-1")
    reject_pair(db, left_listing_id=green.id, right_listing_id=bien.id, confidence=15, reason="Photos differentes")

    assert semantic_candidate_pairs(db, days=30) == []


def test_merge_listings_moves_sources_photos_states_and_comparison_items():
    db = next(_db())
    target = _listing(db, source="green-acres", source_id="ga-1", title="Villa cible")
    duplicate = _listing(db, source="bien-ici", source_id="bi-1", title="Villa doublon")
    user = User(email="x@example.com", display_name="X", password_hash="hash")
    db.add(user)
    db.flush()
    db.add(UserListingState(user_id=user.id, listing_id=target.id, status="new", note="Note cible"))
    db.add(UserListingState(user_id=user.id, listing_id=duplicate.id, status="favorite", note="Note doublon"))
    db.add(ComparisonItem(user_id=user.id, listing_id=duplicate.id))
    db.commit()

    merged = merge_listings(
        db,
        target_listing_id=target.id,
        duplicate_listing_id=duplicate.id,
        confidence=92,
        reason="Même maison confirmée par photos",
        model="gpt-vision",
    )

    assert merged.id == target.id
    assert db.get(Listing, duplicate.id) is None
    assert db.scalar(select(ListingSource).where(ListingSource.source == "bien-ici")).listing_id == target.id
    assert {photo.url for photo in db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == target.id)).all()} == {
        "https://cdn/ga-1.jpg",
        "https://cdn/bi-1.jpg",
    }
    state = db.scalar(select(UserListingState).where(UserListingState.user_id == user.id, UserListingState.listing_id == target.id))
    assert db.scalar(select(ComparisonItem).where(ComparisonItem.user_id == user.id)).listing_id == target.id
    decision = db.scalar(select(SemanticDedupDecision))
    assert decision.status == "merged"
    assert decision.confidence == 92


def test_merge_never_overrides_a_users_own_status_on_conflict():
    """A merge must never pick a status on a user's behalf: each user's own
    choice stays theirs. The conflicting status only surfaces as a note."""
    db = next(_db())
    target = _listing(db, source="green-acres", source_id="ga-1", title="Villa cible")
    duplicate = _listing(db, source="bien-ici", source_id="bi-1", title="Villa doublon")
    user = User(email="x@example.com", display_name="X", password_hash="hash")
    db.add(user)
    db.flush()
    db.add(UserListingState(user_id=user.id, listing_id=target.id, status="favorite", note=None))
    db.add(UserListingState(user_id=user.id, listing_id=duplicate.id, status="rejected", note="Trop cher"))
    db.commit()

    merge_listings(db, target_listing_id=target.id, duplicate_listing_id=duplicate.id, confidence=90)

    state = db.scalar(
        select(UserListingState).where(UserListingState.user_id == user.id, UserListingState.listing_id == target.id)
    )
    assert state.status == "favorite"
    assert "rejetee" in state.note
    assert "Trop cher" in state.note


def test_merge_keeps_new_status_untouched_when_duplicate_has_a_decisive_one():
    """Even the default 'new' status is never auto-upgraded by a merge."""
    db = next(_db())
    target = _listing(db, source="green-acres", source_id="ga-1", title="Villa cible")
    duplicate = _listing(db, source="bien-ici", source_id="bi-1", title="Villa doublon")
    user = User(email="x@example.com", display_name="X", password_hash="hash")
    db.add(user)
    db.flush()
    db.add(UserListingState(user_id=user.id, listing_id=target.id, status="new", note="Note cible"))
    db.add(UserListingState(user_id=user.id, listing_id=duplicate.id, status="favorite", note="Note doublon"))
    db.commit()

    merge_listings(db, target_listing_id=target.id, duplicate_listing_id=duplicate.id, confidence=90)

    state = db.scalar(
        select(UserListingState).where(UserListingState.user_id == user.id, UserListingState.listing_id == target.id)
    )
    assert state.status == "new"
    assert "shortlist" in state.note
    assert "Note cible" in state.note
    assert "Note doublon" in state.note
