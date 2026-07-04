from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ComparisonItem,
    Listing,
    ListingPhoto,
    ListingSource,
    PriceHistory,
    SemanticDedupDecision,
    UserListingState,
)


def pair_ids(left_id: int, right_id: int) -> tuple[int, int]:
    return (left_id, right_id) if left_id < right_id else (right_id, left_id)


def reviewed_pair_exists(db: Session, left_id: int, right_id: int) -> bool:
    left_id, right_id = pair_ids(left_id, right_id)
    return (
        db.scalar(
            select(SemanticDedupDecision.id).where(
                SemanticDedupDecision.left_listing_id == left_id,
                SemanticDedupDecision.right_listing_id == right_id,
            )
        )
        is not None
    )


def semantic_candidate_pairs(db: Session, *, days: int = 14, limit: int = 50) -> list[tuple[Listing, Listing]]:
    """Return same-city listing pairs for an external AI dedup pass.

    The deterministic ingest heuristic already merged obvious cross-source
    duplicates. This intentionally returns broader same-city pairs, biased
    toward recently created listings, so an external multimodal model can judge
    title/description/photos before calling the merge endpoint.
    """
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    recent = list(
        db.scalars(
            select(Listing)
            .where(Listing.created_at >= since)
            .options(selectinload(Listing.sources), selectinload(Listing.photos))
            .order_by(Listing.created_at.desc())
            .limit(limit)
        ).all()
    )
    pairs: list[tuple[Listing, Listing]] = []
    seen: set[tuple[int, int]] = set()

    for listing in recent:
        candidates = list(
            db.scalars(
                select(Listing)
                .where(Listing.id != listing.id, Listing.city == listing.city)
                .options(selectinload(Listing.sources), selectinload(Listing.photos))
                .order_by(Listing.updated_at.desc())
                .limit(25)
            ).all()
        )
        for candidate in candidates:
            pair = pair_ids(listing.id, candidate.id)
            if pair in seen or reviewed_pair_exists(db, *pair):
                continue
            if not _has_different_sources(listing, candidate):
                continue
            seen.add(pair)
            pairs.append((listing, candidate))
            if len(pairs) >= limit:
                return pairs
    return pairs


def merge_listings(
    db: Session,
    *,
    target_listing_id: int,
    duplicate_listing_id: int,
    confidence: int | None = None,
    reason: str | None = None,
    model: str | None = None,
) -> Listing:
    if target_listing_id == duplicate_listing_id:
        raise ValueError("target and duplicate must differ")

    target = _load_listing(db, target_listing_id)
    duplicate = _load_listing(db, duplicate_listing_id)
    if target is None or duplicate is None:
        raise ValueError("listing not found")

    _record_decision(
        db,
        target.id,
        duplicate.id,
        status="merged",
        confidence=confidence,
        reason=reason,
        model=model,
    )
    _merge_sources(db, target, duplicate)
    _merge_photos(db, target, duplicate)
    _move_price_history(db, target, duplicate)
    _merge_user_states(db, target, duplicate)
    _merge_comparison_items(db, target, duplicate)
    _fill_missing_listing_fields(target, duplicate)

    db.flush()
    db.execute(delete(Listing).where(Listing.id == duplicate.id))
    db.commit()
    db.refresh(target)
    return _load_listing(db, target.id) or target


def reject_pair(
    db: Session,
    *,
    left_listing_id: int,
    right_listing_id: int,
    confidence: int | None = None,
    reason: str | None = None,
    model: str | None = None,
) -> SemanticDedupDecision:
    decision = _record_decision(
        db,
        left_listing_id,
        right_listing_id,
        status="rejected",
        confidence=confidence,
        reason=reason,
        model=model,
    )
    db.commit()
    db.refresh(decision)
    return decision


def _load_listing(db: Session, listing_id: int) -> Listing | None:
    return db.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(
            selectinload(Listing.sources),
            selectinload(Listing.photos),
            selectinload(Listing.user_states),
        )
    )


def _has_different_sources(left: Listing, right: Listing) -> bool:
    left_sources = {source.source for source in left.sources}
    right_sources = {source.source for source in right.sources}
    return bool(left_sources and right_sources and left_sources != right_sources)


def _record_decision(
    db: Session,
    left_id: int,
    right_id: int,
    *,
    status: str,
    confidence: int | None,
    reason: str | None,
    model: str | None,
) -> SemanticDedupDecision:
    left_id, right_id = pair_ids(left_id, right_id)
    decision = db.scalar(
        select(SemanticDedupDecision).where(
            SemanticDedupDecision.left_listing_id == left_id,
            SemanticDedupDecision.right_listing_id == right_id,
        )
    )
    if decision is None:
        decision = SemanticDedupDecision(left_listing_id=left_id, right_listing_id=right_id, status=status)
        db.add(decision)
    decision.status = status
    decision.confidence = confidence
    decision.reason = reason
    decision.model = model
    return decision


def _merge_sources(db: Session, target: Listing, duplicate: Listing) -> None:
    existing = {(source.source, source.source_id) for source in target.sources}
    for source in list(duplicate.sources):
        key = (source.source, source.source_id)
        if key in existing:
            db.delete(source)
            continue
        source.listing_id = target.id
        existing.add(key)


def _merge_photos(db: Session, target: Listing, duplicate: Listing) -> None:
    existing_urls = {photo.url for photo in target.photos}
    next_position = len(target.photos)
    for photo in sorted(duplicate.photos, key=lambda item: item.position):
        if photo.url in existing_urls:
            db.delete(photo)
            continue
        db.add(ListingPhoto(listing_id=target.id, url=photo.url, position=next_position))
        next_position += 1
        existing_urls.add(photo.url)
        db.delete(photo)


def _move_price_history(db: Session, target: Listing, duplicate: Listing) -> None:
    for history in db.scalars(select(PriceHistory).where(PriceHistory.listing_id == duplicate.id)).all():
        history.listing_id = target.id


_STATUS_LABELS = {
    "new": "nouvelle",
    "favorite": "shortlist",
    "call": "a appeler",
    "rejected": "rejetee",
}


def _merge_user_states(db: Session, target: Listing, duplicate: Listing) -> None:
    duplicate_states = list(
        db.scalars(select(UserListingState).where(UserListingState.listing_id == duplicate.id)).all()
    )
    for duplicate_state in duplicate_states:
        target_state = db.scalar(
            select(UserListingState).where(
                UserListingState.user_id == duplicate_state.user_id,
                UserListingState.listing_id == target.id,
            )
        )
        if target_state is None:
            # This user only had an opinion on the duplicate ad: nothing to
            # reconcile, just carry it over onto the surviving listing.
            duplicate_state.listing_id = target.id
            continue
        # Both ads carry this user's own state. A merge must never pick a
        # status on their behalf -- each user's status is theirs to set.
        # Keep whatever they already chose on the surviving listing, and
        # surface the duplicate's differing status/note as a visible flag so
        # they can review and re-pick themselves if they want to.
        extra = duplicate_state.note
        if duplicate_state.status != target_state.status:
            label = _STATUS_LABELS.get(duplicate_state.status, duplicate_state.status)
            flag = f"Statut '{label}' sur l'annonce fusionnee (non applique automatiquement)"
            extra = f"{flag}\n{extra}" if extra else flag
        target_state.note = _merge_notes(target_state.note, extra)
        target_state.updated_at = max(target_state.updated_at, duplicate_state.updated_at)
        db.delete(duplicate_state)


def _merge_comparison_items(db: Session, target: Listing, duplicate: Listing) -> None:
    duplicate_items = list(db.scalars(select(ComparisonItem).where(ComparisonItem.listing_id == duplicate.id)).all())
    for duplicate_item in duplicate_items:
        target_item = db.scalar(
            select(ComparisonItem).where(
                ComparisonItem.user_id == duplicate_item.user_id,
                ComparisonItem.listing_id == target.id,
            )
        )
        if target_item is None:
            duplicate_item.listing_id = target.id
        else:
            db.delete(duplicate_item)


def _fill_missing_listing_fields(target: Listing, duplicate: Listing) -> None:
    for field in (
        "postal_code",
        "price_eur",
        "living_area_m2",
        "land_area_m2",
        "rooms",
        "bedrooms",
        "energy_rating",
        "description",
        "score",
    ):
        if getattr(target, field) is None and getattr(duplicate, field) is not None:
            setattr(target, field, getattr(duplicate, field))
    target.updated_at = max(target.updated_at, duplicate.updated_at)


def _merge_notes(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right or right in left:
        return left
    return f"{left}\n\n--- Note annonce fusionnee ---\n{right}"
