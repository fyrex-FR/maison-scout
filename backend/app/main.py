from collections import defaultdict
from datetime import datetime
import hashlib
import json
import secrets
import statistics

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import create_token, get_current_user, hash_password, parse_token, verify_password
from app.cities import CITY_METADATA, canonical_city_name
from app.cities import city_slug
from app.config import settings
from app.crawlers.base import CrawledListing
from app.crawlers.bien_ici import BienIciCrawler
from app.crawlers.demo import DemoCrawler
from app.crawlers.green_acres import GreenAcresCrawler
from app.crawlers.pap import PapCrawler
from app.crawlers.paruvendu import ParuVenduCrawler
from app.db import get_db
from app.enrichment.dvf import refresh_city_stats
from app.enrichment.georisques import enrich_listings_risks
from app.ingest import ExternalBatchCrawler, run_crawler
from app.insights import auto_flags, price_insight
from app.lifecycle import refresh_off_market_status
from app.models import (
    CityMarketStat,
    ComparisonItem,
    CrawlRun,
    InviteCode,
    Listing,
    ListingAIAnalysis,
    ListingMatchScore,
    NaturalSearchProfile,
    PriceHistory,
    SearchProfile,
    SemanticDedupDecision,
    User,
    UserListingState,
)
from app.schemas import (
    AdminUserOut,
    AIAnalysisCandidateOut,
    AuthRequest,
    AuthResponse,
    CrawlRunOut,
    InviteCodeCreate,
    InviteCodeOut,
    InviteCodeUpdate,
    IngestBatchIn,
    ListingAIAnalysisOut,
    ListingAIAnalysisWrite,
    ListingMatchScoreOut,
    ListingMatchScoreWrite,
    ListingOut,
    ListingStatusUpdate,
    NaturalSearchProfileCreate,
    NaturalSearchProfileOut,
    NaturalSearchProfileParseUpdate,
    NaturalSearchProfileUpdate,
    PendingMatchPairOut,
    PriceHistoryPointOut,
    ProtectedSourceTargetOut,
    ScoreFactor,
    SemanticDedupCandidateOut,
    SemanticDedupDecisionOut,
    SemanticDedupDecisionRequest,
    SearchProfileCreate,
    SearchProfileOut,
    UserOut,
)
from app.semantic_dedup import merge_listings, reject_pair, semantic_candidate_pairs

app = FastAPI(title="Maison Scout API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Le schéma est géré par Alembic (voir docs/deployment.md).
# Le conteneur exécute `alembic upgrade head` au démarrage (CMD du Dockerfile),
# avant de lancer uvicorn — plus de create_all / ALTER à la volée ici.


def is_user_admin(user: User) -> bool:
    """A user is admin either via the DB flag or via the ADMIN_EMAILS env,
    so the app owner can become admin without any manual DB manipulation.
    """
    return bool(user.is_admin) or user.email.strip().lower() in settings.admin_email_set


def require_admin(user: User) -> None:
    if not is_user_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")


def user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=is_user_admin(user),
    )


@app.get("/health")
def health(response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(select(1))
    except Exception:
        response.status_code = 503
        return {"status": "degraded"}
    return {"status": "ok"}


def _validate_invite_code(db: Session, provided: str) -> InviteCode | None:
    """Returns the matching active DB invite code (if any) after validating.

    Invitation is required as soon as either the env invite_code_set is
    non-empty or at least one active InviteCode row exists in the DB.
    Raises 403 (message unchanged) if required and not satisfied.
    """
    env_codes = settings.invite_code_set
    db_code = db.scalar(select(InviteCode).where(InviteCode.code == provided, InviteCode.active == True))  # noqa: E712
    any_active_db_code = db.scalar(select(InviteCode.id).where(InviteCode.active == True)) is not None  # noqa: E712
    invitation_required = bool(env_codes) or any_active_db_code
    valid = (provided in env_codes) or (db_code is not None)
    if invitation_required and not valid:
        raise HTTPException(status_code=403, detail="Invalid invite code")
    return db_code


@app.post("/api/auth/register", response_model=AuthResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    if not settings.allow_open_registration:
        raise HTTPException(status_code=403, detail="Registration disabled")
    provided = (payload.invite_code or "").strip()
    db_code = _validate_invite_code(db, provided)
    email = payload.email.strip().lower()
    if not email or len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Email and 8+ char password required")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=email,
        display_name=payload.display_name or email.split("@")[0],
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    db.add(SearchProfile(user_id=user.id, name="Frejus / Saint-Raphael", city="Frejus"))
    db.add(SearchProfile(user_id=user.id, name="Frejus / Saint-Raphael", city="Saint-Raphael"))
    if db_code is not None:
        db_code.used_count += 1
    db.commit()
    db.refresh(user)
    return AuthResponse(token=create_token(user), user=user_to_out(user))


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return AuthResponse(token=create_token(user), user=user_to_out(user))


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return user_to_out(user)


@app.get("/api/listings", response_model=list[ListingOut])
def list_listings(
    include_off_market: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Listing]:
    stmt = (
        select(Listing)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
        .order_by(Listing.score.desc().nullslast(), Listing.updated_at.desc())
    )
    listings = list(db.scalars(stmt).all())
    profile_cities = {
        canonical_city_name(city)
        for city in db.scalars(
            select(SearchProfile.city).where(SearchProfile.user_id == user.id, SearchProfile.enabled == True)  # noqa: E712
        ).all()
    }
    if profile_cities:
        listings = [listing for listing in listings if canonical_city_name(listing.city) in profile_cities]
    listings = [listing for listing in listings if listing_matches_any_profile(listing, profiles_for_user(db, user))]
    if not include_off_market:
        # An off-market listing the user has favorited/marked-to-call must
        # stay visible (labeled "retirée") rather than silently vanish --
        # only drop off-market listings with no such kept-alive user state.
        kept_alive_listing_ids = {
            state.listing_id
            for state in db.scalars(
                select(UserListingState).where(
                    UserListingState.user_id == user.id,
                    UserListingState.status.in_(["favorite", "call"]),
                )
            ).all()
        }
        listings = [
            listing
            for listing in listings
            if listing.off_market_at is None or listing.id in kept_alive_listing_ids
        ]
    return attach_user_context(db, user, listings)


@app.post("/api/listings/mark-seen")
def mark_listings_seen(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user.listings_seen_at = datetime.utcnow()
    db.commit()
    return {"status": "ok", "listings_seen_at": user.listings_seen_at.isoformat()}


def attach_user_context(db: Session, user: User, listings: list[Listing]) -> list[Listing]:
    """Attach per-user status/note/score onto listings not owned by the DB row itself."""
    profiles = profiles_for_user(db, user)
    listing_ids = [listing.id for listing in listings]
    states = {
        state.listing_id: state
        for state in db.scalars(
            select(UserListingState).where(
                UserListingState.user_id == user.id,
                UserListingState.listing_id.in_(listing_ids),
            )
        ).all()
    } if listing_ids else {}

    active_profile = active_natural_search_profile(db, user)
    ai_analyses = ai_analyses_by_listing_id(db, listing_ids)
    match_scores = match_scores_by_listing_id(db, listing_ids, active_profile)
    price_histories = price_history_by_listing_id(db, listing_ids)
    city_median_price_per_m2 = median_price_per_m2_by_city(listings)
    market_stats_by_city = market_stats_by_city_for_listings(db, listings)

    for listing in listings:
        state = states.get(listing.id)
        listing.status = state.status if state else "new"
        listing.note = state.note if state else None
        score, breakdown = score_for_user(listing, profiles)
        listing.score = score
        listing.score_breakdown = breakdown

        analysis = ai_analyses.get(listing.id)
        listing.ai_summary = analysis.summary if analysis else None
        listing.red_flags = analysis.red_flags_json if analysis else []

        match = match_scores.get(listing.id)
        listing.match_score = match.score if match else None
        listing.match_reasons = match.matched_reasons_json if match else []
        listing.match_missing = match.missing_or_uncertain_json if match else []
        listing.match_dealbreakers = match.dealbreakers_json if match else []
        listing.active_profile_name = active_profile.name if active_profile else None

        is_off_market = listing.off_market_at is not None
        listing.off_market = is_off_market
        end_of_market_period = listing.off_market_at if is_off_market else datetime.utcnow()
        listing.days_on_market = max(0, (end_of_market_period - listing.created_at).days)
        # Negotiation-lever signal only makes sense for still-active listings.
        days_on_market_for_flags = None if is_off_market else listing.days_on_market

        city_median = city_median_price_per_m2.get(canonical_city_name(listing.city))

        market_stat = market_stats_by_city.get(canonical_city_name(listing.city))
        listing.dvf_median_price_per_m2 = market_stat.median_price_per_m2_house if market_stat else None
        listing.dvf_period = market_stat.period_label if market_stat else None
        listing.dvf_delta_ratio = None
        if (
            market_stat is not None
            and market_stat.median_price_per_m2_house
            and listing.price_eur
            and listing.living_area_m2
        ):
            listing_price_per_m2 = listing.price_eur / listing.living_area_m2
            listing.dvf_delta_ratio = round(
                (listing_price_per_m2 / market_stat.median_price_per_m2_house) - 1, 3
            )
        listing.risks = listing.georisques_json

        listing.auto_flags = auto_flags(
            listing,
            city_median_price_per_m2=city_median,
            days_on_market=days_on_market_for_flags,
            dvf_delta_ratio=listing.dvf_delta_ratio,
            risks=listing.risks,
        )
        prices_chronological = price_histories.get(listing.id, [])
        insight = price_insight(prices_chronological, listing.price_eur)
        listing.price_dropped = insight["dropped"]
        listing.price_change_abs = insight["change_abs"]
        listing.price_observations = insight["count"]

        listing.is_new = (user.listings_seen_at is None) or (listing.created_at > user.listings_seen_at)
    return listings


def price_history_by_listing_id(db: Session, listing_ids: list[int]) -> dict[int, list[int]]:
    """Chronological (oldest -> newest) price points per listing, in one query."""
    if not listing_ids:
        return {}
    stmt = (
        select(PriceHistory.listing_id, PriceHistory.price_eur)
        .where(PriceHistory.listing_id.in_(listing_ids))
        .order_by(PriceHistory.observed_at)
    )
    prices_by_listing_id: dict[int, list[int]] = defaultdict(list)
    for listing_id, price_eur in db.execute(stmt).all():
        prices_by_listing_id[listing_id].append(price_eur)
    return dict(prices_by_listing_id)


def median_price_per_m2_by_city(listings: list[Listing]) -> dict[str, float]:
    """Median price/m2 per canonical city, computed only from the displayed
    listings that have both a price and a living area (0 treated as unknown).
    """
    prices_per_m2_by_city: dict[str, list[float]] = defaultdict(list)
    for listing in listings:
        if not listing.price_eur or not listing.living_area_m2:
            continue
        city = canonical_city_name(listing.city)
        prices_per_m2_by_city[city].append(listing.price_eur / listing.living_area_m2)
    return {city: statistics.median(values) for city, values in prices_per_m2_by_city.items()}


def market_stats_by_city_for_listings(db: Session, listings: list[Listing]) -> dict[str, CityMarketStat]:
    """Load CityMarketStat rows for the displayed listings' cities, one query."""
    cities = {canonical_city_name(listing.city) for listing in listings}
    if not cities:
        return {}
    stmt = select(CityMarketStat).where(CityMarketStat.city.in_(cities))
    return {stat.city: stat for stat in db.scalars(stmt).all()}


def active_natural_search_profile(db: Session, user: User) -> NaturalSearchProfile | None:
    """The user's single active natural-search profile, if any.

    Multiple active profiles are possible in principle; we pick the most
    recently updated one as "the" active profile for match display purposes.
    """
    stmt = (
        select(NaturalSearchProfile)
        .where(NaturalSearchProfile.user_id == user.id, NaturalSearchProfile.is_active == True)  # noqa: E712
        .order_by(NaturalSearchProfile.updated_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def ai_analyses_by_listing_id(db: Session, listing_ids: list[int]) -> dict[int, ListingAIAnalysis]:
    if not listing_ids:
        return {}
    stmt = select(ListingAIAnalysis).where(ListingAIAnalysis.listing_id.in_(listing_ids))
    return {analysis.listing_id: analysis for analysis in db.scalars(stmt).all()}


def match_scores_by_listing_id(
    db: Session, listing_ids: list[int], active_profile: NaturalSearchProfile | None
) -> dict[int, ListingMatchScore]:
    if not listing_ids or active_profile is None:
        return {}
    stmt = select(ListingMatchScore).where(
        ListingMatchScore.listing_id.in_(listing_ids),
        ListingMatchScore.natural_search_profile_id == active_profile.id,
    )
    return {score.listing_id: score for score in db.scalars(stmt).all()}


def profiles_for_user(db: Session, user: User) -> list[SearchProfile]:
    return list(
        db.scalars(select(SearchProfile).where(SearchProfile.user_id == user.id, SearchProfile.enabled == True)).all()  # noqa: E712
    )


def listing_matches_any_profile(listing: Listing, profiles: list[SearchProfile]) -> bool:
    matching = [profile for profile in profiles if canonical_city_name(profile.city) == canonical_city_name(listing.city)]
    if not matching:
        return False
    return any(listing_matches_profile(listing, profile) for profile in matching)


def listing_matches_profile(listing: Listing, profile: SearchProfile) -> bool:
    """A listing is excluded only when a criterion is present AND violated.

    Crawlers sometimes fail to extract fields (bedrooms, land area, ...). A
    missing value must never exclude a listing -- we'd rather show a possibly
    matching listing than hide a good one because of an extraction gap. Only
    exclude when we positively know the listing violates the criterion.
    """
    if (
        profile.max_price_eur is not None
        and listing.price_eur is not None
        and listing.price_eur > profile.max_price_eur
    ):
        return False
    if (
        profile.min_living_area_m2 is not None
        and listing.living_area_m2 is not None
        and listing.living_area_m2 < profile.min_living_area_m2
    ):
        return False
    if (
        profile.min_land_area_m2 is not None
        and listing.land_area_m2 is not None
        and listing.land_area_m2 < profile.min_land_area_m2
    ):
        return False
    if (
        profile.min_bedrooms is not None
        and listing.bedrooms is not None
        and listing.bedrooms < profile.min_bedrooms
    ):
        return False
    return True


def score_for_user(listing: Listing, profiles: list[SearchProfile]) -> tuple[int, list[ScoreFactor]]:
    matching = [profile for profile in profiles if canonical_city_name(profile.city) == canonical_city_name(listing.city)]
    profile = matching[0] if matching else None
    base_score = listing.score or 50
    breakdown: list[ScoreFactor] = [ScoreFactor(label="Score de base", delta=base_score)]
    score = base_score
    if not profile:
        return score, breakdown
    if profile.max_price_eur and listing.price_eur:
        if listing.price_eur <= profile.max_price_eur:
            delta = 12
            breakdown.append(ScoreFactor(label="Sous le budget max", delta=delta))
        else:
            delta = -18
            breakdown.append(ScoreFactor(label="Au-dessus du budget max", delta=delta))
        score += delta
    if profile.min_living_area_m2 and listing.living_area_m2:
        if listing.living_area_m2 >= profile.min_living_area_m2:
            delta = 8
            breakdown.append(ScoreFactor(label="Surface habitable suffisante", delta=delta))
        else:
            delta = -10
            breakdown.append(ScoreFactor(label="Surface habitable trop petite", delta=delta))
        score += delta
    if profile.min_land_area_m2 and listing.land_area_m2:
        if listing.land_area_m2 >= profile.min_land_area_m2:
            delta = 8
            breakdown.append(ScoreFactor(label="Terrain suffisant", delta=delta))
        else:
            delta = -8
            breakdown.append(ScoreFactor(label="Terrain trop petit", delta=delta))
        score += delta
    if profile.min_bedrooms and listing.bedrooms:
        if listing.bedrooms >= profile.min_bedrooms:
            delta = 8
            breakdown.append(ScoreFactor(label="Assez de chambres", delta=delta))
        else:
            delta = -10
            breakdown.append(ScoreFactor(label="Pas assez de chambres", delta=delta))
        score += delta
    return max(0, min(100, score)), breakdown


@app.patch("/api/listings/{listing_id}/status", response_model=ListingOut)
def update_listing_status(
    listing_id: int,
    payload: ListingStatusUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    state = db.scalar(
        select(UserListingState).where(
            UserListingState.user_id == user.id,
            UserListingState.listing_id == listing_id,
        )
    )
    if state is None:
        state = UserListingState(user_id=user.id, listing_id=listing_id)
        db.add(state)
    fields_set = payload.model_fields_set
    if "status" in fields_set and payload.status is not None:
        state.status = payload.status
    elif state.status is None:
        state.status = "new"
    if "note" in fields_set:
        state.note = payload.note
    db.commit()
    stmt = (
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
    )
    listing = db.scalar(stmt)
    listing.status = state.status
    listing.note = state.note
    return listing


@app.get("/api/listings/{listing_id}/price-history", response_model=list[PriceHistoryPointOut])
def get_listing_price_history(
    listing_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PriceHistory]:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.listing_id == listing_id)
        .order_by(PriceHistory.observed_at)
    )
    return list(db.scalars(stmt).all())


COMPARISON_LIMIT = 4


@app.get("/api/comparison", response_model=list[ListingOut])
def list_comparison(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Listing]:
    stmt = (
        select(Listing)
        .join(ComparisonItem, ComparisonItem.listing_id == Listing.id)
        .where(ComparisonItem.user_id == user.id)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
        .order_by(ComparisonItem.added_at)
    )
    listings = list(db.scalars(stmt).all())
    return attach_user_context(db, user, listings)


@app.post("/api/comparison/{listing_id}", response_model=list[ListingOut])
def add_to_comparison(
    listing_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Listing]:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    existing = db.scalar(
        select(ComparisonItem).where(ComparisonItem.user_id == user.id, ComparisonItem.listing_id == listing_id)
    )
    if existing is None:
        # Insert first, then verify the count on the committed state rather than
        # check-then-insert: two concurrent adds can both pass a pre-insert count
        # check before either commits, silently exceeding the limit.
        item = ComparisonItem(user_id=user.id, listing_id=listing_id)
        db.add(item)
        db.commit()
        count = db.scalar(
            select(func.count()).select_from(ComparisonItem).where(ComparisonItem.user_id == user.id)
        )
        if count > COMPARISON_LIMIT:
            db.delete(item)
            db.commit()
            raise HTTPException(status_code=400, detail=f"Comparatif complet ({COMPARISON_LIMIT} annonces maximum)")
    return list_comparison(user, db)


@app.delete("/api/comparison/{listing_id}", response_model=list[ListingOut])
def remove_from_comparison(
    listing_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Listing]:
    existing = db.scalar(
        select(ComparisonItem).where(ComparisonItem.user_id == user.id, ComparisonItem.listing_id == listing_id)
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
    return list_comparison(user, db)


@app.get("/api/search-profiles", response_model=list[SearchProfileOut])
def list_search_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SearchProfile]:
    stmt = select(SearchProfile).where(SearchProfile.user_id == user.id).order_by(SearchProfile.city)
    return list(db.scalars(stmt).all())


@app.post("/api/search-profiles", response_model=SearchProfileOut)
def create_search_profile(
    payload: SearchProfileCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SearchProfile:
    city = payload.city.strip()
    if not city:
        raise HTTPException(status_code=400, detail="City required")
    profile = SearchProfile(
        user_id=user.id,
        name=payload.name or city,
        city=city,
        source=payload.source,
        enabled=True,
        max_price_eur=payload.max_price_eur,
        min_living_area_m2=payload.min_living_area_m2,
        min_land_area_m2=payload.min_land_area_m2,
        min_bedrooms=payload.min_bedrooms,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@app.patch("/api/search-profiles/{profile_id}", response_model=SearchProfileOut)
def update_search_profile(
    profile_id: int,
    payload: SearchProfileCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SearchProfile:
    profile = db.get(SearchProfile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Search profile not found")
    profile.name = payload.name or payload.city.strip()
    profile.city = payload.city.strip()
    profile.source = payload.source
    profile.max_price_eur = payload.max_price_eur
    profile.min_living_area_m2 = payload.min_living_area_m2
    profile.min_land_area_m2 = payload.min_land_area_m2
    profile.min_bedrooms = payload.min_bedrooms
    db.commit()
    db.refresh(profile)
    return profile


@app.delete("/api/search-profiles/{profile_id}")
def delete_search_profile(
    profile_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    profile = db.get(SearchProfile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Search profile not found")
    db.delete(profile)
    db.commit()
    return {"status": "deleted"}


@app.get("/api/natural-search-profiles", response_model=list[NaturalSearchProfileOut])
def list_natural_search_profiles(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NaturalSearchProfile]:
    stmt = (
        select(NaturalSearchProfile)
        .where(NaturalSearchProfile.user_id == user.id)
        .order_by(NaturalSearchProfile.created_at)
    )
    return list(db.scalars(stmt).all())


@app.post("/api/natural-search-profiles", response_model=NaturalSearchProfileOut)
def create_natural_search_profile(
    payload: NaturalSearchProfileCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NaturalSearchProfile:
    raw_prompt = payload.raw_prompt.strip()
    if not raw_prompt:
        raise HTTPException(status_code=400, detail="Prompt required")
    profile = NaturalSearchProfile(
        user_id=user.id,
        name=(payload.name or "Recherche principale").strip(),
        raw_prompt=raw_prompt,
        is_active=payload.is_active,
        criteria_json={},
        weights_json={},
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@app.patch("/api/natural-search-profiles/{profile_id}", response_model=NaturalSearchProfileOut)
def update_natural_search_profile(
    profile_id: int,
    payload: NaturalSearchProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NaturalSearchProfile:
    profile = db.get(NaturalSearchProfile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Natural search profile not found")
    if payload.name is not None:
        profile.name = payload.name.strip() or profile.name
    if payload.raw_prompt is not None:
        raw_prompt = payload.raw_prompt.strip()
        if not raw_prompt:
            raise HTTPException(status_code=400, detail="Prompt required")
        profile.raw_prompt = raw_prompt
        profile.criteria_json = {}
        profile.weights_json = {}
        profile.parsed_model = None
        profile.parsed_at = None
    if payload.is_active is not None:
        profile.is_active = payload.is_active
    db.commit()
    db.refresh(profile)
    return profile


@app.delete("/api/natural-search-profiles/{profile_id}")
def delete_natural_search_profile(
    profile_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    profile = db.get(NaturalSearchProfile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Natural search profile not found")
    db.delete(profile)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/crawl/demo")
async def crawl_demo(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    require_crawl_access(authorization, x_crawl_secret)
    run: CrawlRun = await run_crawler(db, DemoCrawler())
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/green-acres")
async def crawl_green_acres(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    require_crawl_access(authorization, x_crawl_secret)
    cities = active_search_cities(db)
    run: CrawlRun = await run_crawler(db, GreenAcresCrawler.from_cities(cities))
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/pap")
# opt-in: pas encore dans /crawl/all ni le cron — à valider manuellement d'abord
async def crawl_pap(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    require_crawl_access(authorization, x_crawl_secret)
    cities = active_search_cities(db)
    run: CrawlRun = await run_crawler(db, PapCrawler.from_cities(cities))
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/paruvendu")
# opt-in: pas encore dans /crawl/all ni le cron -- a valider manuellement d'abord
async def crawl_paruvendu(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    require_crawl_access(authorization, x_crawl_secret)
    cities = active_search_cities(db)
    run: CrawlRun = await run_crawler(db, ParuVenduCrawler.from_cities(cities))
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/bien-ici")
async def crawl_bien_ici(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    require_crawl_access(authorization, x_crawl_secret)
    run: CrawlRun = await run_crawler(db, BienIciCrawler.from_cities(active_search_cities(db)))
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/all")
async def crawl_all(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict[str, int | str | list[dict[str, int | str]]]:
    require_crawl_access(authorization, x_crawl_secret)
    cities = active_search_cities(db)
    crawlers = [GreenAcresCrawler.from_cities(cities), BienIciCrawler.from_cities(cities)]
    runs = []
    for crawler in crawlers:
        run = await run_crawler(db, crawler)
        runs.append({"source": run.source, "status": run.status, "found_count": run.found_count})
    lifecycle_result = refresh_off_market_status(db)
    db.commit()
    return {
        "status": "ok" if all(run["status"] == "ok" for run in runs) else "partial",
        "found_count": sum(int(run["found_count"]) for run in runs),
        "runs": runs,
        "marked_off_market": lifecycle_result["marked_off_market"],
    }


@app.post("/api/enrich/all")
async def enrich_all(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_crawl_secret: str | None = Header(default=None),
) -> dict:
    """Refresh open-data enrichment: DVF market stats + Georisques risks.

    Meant to be triggered by the same cron as /api/crawl/all (same auth
    model: X-Crawl-Secret or a valid user bearer token). Both passes talk to
    third-party open-data APIs that are outside our control, so a failure in
    either one is caught and reported in the response rather than bubbling
    up as a 500 -- a flaky external API should never break the cron.
    """
    require_crawl_access(authorization, x_crawl_secret)

    try:
        dvf_result = await refresh_city_stats(db, active_search_cities(db))
    except Exception as exc:
        dvf_result = {"refreshed": 0, "skipped": 0, "failed": 0, "error": str(exc)}

    try:
        risks_result = await enrich_listings_risks(db)
    except Exception as exc:
        risks_result = {"checked": 0, "failed": 0, "error": str(exc)}

    return {"dvf": dvf_result, "risks": risks_result}


MAX_INGEST_BATCH_SIZE = 500

# Backward-compatible alias: this table now lives in app.cities as
# CITY_METADATA (single source of truth, also used by the ParuVendu crawler
# to build its city+postal-code search URL slugs).
PROTECTED_SOURCE_CITY_METADATA = CITY_METADATA


@app.get("/api/ingest/protected-source-targets", response_model=list[ProtectedSourceTargetOut])
def protected_source_targets(
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[ProtectedSourceTargetOut]:
    """Active city targets for external browser scrapers such as OpenClaw.

    Green-Acres/Bien'ici run inside the backend and already read active
    SearchProfile rows. PAP/SeLoger run outside the backend, so they need a
    secret-only discovery endpoint instead of hardcoded city lists.
    """
    require_crawl_secret(x_crawl_secret)

    profiles = list(
        db.scalars(
            select(SearchProfile)
            .where(SearchProfile.enabled == True)  # noqa: E712
            .order_by(SearchProfile.city)
        ).all()
    )
    by_city: dict[str, list[SearchProfile]] = defaultdict(list)
    for profile in profiles:
        city = canonical_city_name(profile.city)
        if city:
            by_city[city].append(profile)

    targets: list[ProtectedSourceTargetOut] = []
    for city, city_profiles in sorted(by_city.items()):
        metadata = PROTECTED_SOURCE_CITY_METADATA.get(city, {})
        slug = city_slug(city)
        postal_code = metadata.get("postal_code")
        seloger_department = metadata.get("seloger_department")
        targets.append(
            ProtectedSourceTargetOut(
                city=city,
                postal_code=postal_code,
                max_price_eur=min(
                    (profile.max_price_eur for profile in city_profiles if profile.max_price_eur is not None),
                    default=None,
                ),
                min_living_area_m2=max(
                    (profile.min_living_area_m2 for profile in city_profiles if profile.min_living_area_m2 is not None),
                    default=None,
                ),
                min_land_area_m2=max(
                    (profile.min_land_area_m2 for profile in city_profiles if profile.min_land_area_m2 is not None),
                    default=None,
                ),
                min_bedrooms=max(
                    (profile.min_bedrooms for profile in city_profiles if profile.min_bedrooms is not None),
                    default=None,
                ),
                pap_slug=f"{slug}-{postal_code}" if postal_code else slug,
                seloger_slug=f"{slug}-{postal_code}" if postal_code else None,
                seloger_department=seloger_department,
            )
        )
    return targets


@app.post("/api/ingest/listings")
async def ingest_listings(
    payload: IngestBatchIn,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Generic ingestion endpoint for external scrapers (e.g. OpenClaw).

    Sources like PAP or SeLoger are protected by Cloudflare/DataDome and
    can't be scraped from this backend's process. An external scraper with a
    real browser fetches the listings and POSTs the already-extracted data
    here. From this point on it flows through the exact same pipeline as any
    in-process crawler (ExternalBatchCrawler -> run_crawler -> upsert_listing
    for each item), so dedup, scoring, photo refresh, price history and city
    normalization are all handled automatically -- no separate logic here.
    """
    require_crawl_secret(x_crawl_secret)  # server secret only, no user bearer token

    source = payload.source.strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    if not payload.items:
        raise HTTPException(status_code=400, detail="items must not be empty")
    if len(payload.items) > MAX_INGEST_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"items exceeds the maximum batch size of {MAX_INGEST_BATCH_SIZE}",
        )

    items = [
        CrawledListing(
            source=source,
            source_id=item.source_id,
            url=item.url,
            title=item.title,
            city=item.city,
            postal_code=item.postal_code,
            price_eur=item.price_eur,
            living_area_m2=item.living_area_m2,
            land_area_m2=item.land_area_m2,
            rooms=item.rooms,
            bedrooms=item.bedrooms,
            energy_rating=item.energy_rating,
            description=item.description,
            photos=item.photos or [],
            latitude=item.latitude,
            longitude=item.longitude,
        )
        for item in payload.items
    ]

    run: CrawlRun = await run_crawler(db, ExternalBatchCrawler(source, items))
    # 48h default threshold guards against false positives here: this endpoint
    # ingests one source at a time, so a listing only carried by a source that
    # hasn't been re-ingested yet must not look "stale" after a single batch.
    lifecycle_result = refresh_off_market_status(db)
    db.commit()
    return {
        "status": run.status,
        "source": run.source,
        "found_count": run.found_count,
        "error": run.error,
        "marked_off_market": lifecycle_result["marked_off_market"],
    }


@app.get("/api/semantic-dedup/candidates", response_model=list[SemanticDedupCandidateOut])
def list_semantic_dedup_candidates(
    days: int = 14,
    limit: int = 50,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_crawl_secret(x_crawl_secret)
    return [_dedup_candidate_payload(left, right) for left, right in semantic_candidate_pairs(db, days=days, limit=limit)]


@app.post("/api/semantic-dedup/merge", response_model=ListingOut)
def merge_semantic_duplicate(
    payload: SemanticDedupDecisionRequest,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Listing:
    require_crawl_secret(x_crawl_secret)
    if not payload.target_listing_id or not payload.duplicate_listing_id:
        raise HTTPException(status_code=400, detail="target_listing_id and duplicate_listing_id are required")
    try:
        listing = merge_listings(
            db,
            target_listing_id=payload.target_listing_id,
            duplicate_listing_id=payload.duplicate_listing_id,
            confidence=payload.confidence,
            reason=payload.reason,
            model=payload.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    listing.status = "new"
    listing.note = None
    listing.score_breakdown = None
    return listing


@app.post("/api/semantic-dedup/reject", response_model=SemanticDedupDecisionOut)
def reject_semantic_duplicate(
    payload: SemanticDedupDecisionRequest,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> SemanticDedupDecision:
    require_crawl_secret(x_crawl_secret)
    if not payload.left_listing_id or not payload.right_listing_id:
        raise HTTPException(status_code=400, detail="left_listing_id and right_listing_id are required")
    return reject_pair(
        db,
        left_listing_id=payload.left_listing_id,
        right_listing_id=payload.right_listing_id,
        confidence=payload.confidence,
        reason=payload.reason,
        model=payload.model,
    )


@app.get("/api/ai/listings/pending-analysis", response_model=list[AIAnalysisCandidateOut])
def list_pending_ai_analysis(
    limit: int = 25,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_crawl_secret(x_crawl_secret)
    limit = max(1, min(limit, 100))
    stmt = (
        select(Listing)
        .options(selectinload(Listing.sources), selectinload(Listing.photos), selectinload(Listing.ai_analysis))
        .order_by(Listing.updated_at.desc())
    )
    candidates = []
    for listing in db.scalars(stmt).all():
        source_hash = ai_listing_source_hash(listing)
        if listing.ai_analysis is None or listing.ai_analysis.source_hash != source_hash:
            candidates.append({"source_hash": source_hash, "current_analysis": listing.ai_analysis, **listing_payload(listing)})
        if len(candidates) >= limit:
            break
    return candidates


@app.get("/api/ai/natural-search-profiles/pending-parse", response_model=list[NaturalSearchProfileOut])
def list_pending_parse_natural_search_profiles(
    limit: int = 50,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[NaturalSearchProfile]:
    require_crawl_secret(x_crawl_secret)
    limit = max(1, min(limit, 200))
    stmt = (
        select(NaturalSearchProfile)
        .where(
            NaturalSearchProfile.is_active == True,  # noqa: E712
            NaturalSearchProfile.parsed_at.is_(None),
        )
        .order_by(NaturalSearchProfile.created_at)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


@app.get("/api/ai/pending-match-scores", response_model=list[PendingMatchPairOut])
def list_pending_match_scores(
    limit: int = 100,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_crawl_secret(x_crawl_secret)
    limit = max(1, min(limit, 500))

    active_profiles = list(
        db.scalars(
            select(NaturalSearchProfile)
            .where(
                NaturalSearchProfile.is_active == True,  # noqa: E712
                NaturalSearchProfile.parsed_at.is_not(None),
            )
            .order_by(NaturalSearchProfile.id)
        ).all()
    )
    if not active_profiles:
        return []

    # Preload every listing that has an AI analysis (analysis is per-listing,
    # unique), keyed by listing_id -- avoids re-querying analyses per profile.
    analyses_by_listing_id = {
        analysis.listing_id: analysis
        for analysis in db.scalars(select(ListingAIAnalysis)).all()
    }
    analyzed_listing_ids = list(analyses_by_listing_id.keys())
    if not analyzed_listing_ids:
        return []
    listings = list(
        db.scalars(
            select(Listing).where(Listing.id.in_(analyzed_listing_ids)).order_by(Listing.id)
        ).all()
    )

    # Preload existing match scores for these listings and the active profiles
    # in one query, keyed by (listing_id, profile_id) -- avoids N+1 lookups.
    active_profile_ids = [profile.id for profile in active_profiles]
    existing_scores = {
        (score.listing_id, score.natural_search_profile_id): score
        for score in db.scalars(
            select(ListingMatchScore).where(
                ListingMatchScore.listing_id.in_(analyzed_listing_ids),
                ListingMatchScore.natural_search_profile_id.in_(active_profile_ids),
            )
        ).all()
    }

    # Classic (enabled) search profiles per owning user, loaded once per user.
    classic_profiles_by_user_id: dict[int, list[SearchProfile]] = {}

    pairs: list[dict] = []
    for profile in active_profiles:
        classic_profiles = classic_profiles_by_user_id.get(profile.user_id)
        if classic_profiles is None:
            classic_profiles = list(
                db.scalars(
                    select(SearchProfile).where(
                        SearchProfile.user_id == profile.user_id,
                        SearchProfile.enabled == True,  # noqa: E712
                    )
                ).all()
            )
            classic_profiles_by_user_id[profile.user_id] = classic_profiles
        for listing in listings:
            if not listing_matches_any_profile(listing, classic_profiles):
                continue
            analysis = analyses_by_listing_id[listing.id]
            existing = existing_scores.get((listing.id, profile.id))
            if existing is not None and existing.source_analysis_id == analysis.id:
                continue  # up-to-date score already exists
            pairs.append(
                {
                    "listing_id": listing.id,
                    "natural_search_profile_id": profile.id,
                    "source_analysis_id": analysis.id,
                    "source_analysis": analysis,
                    "natural_search_profile": profile,
                }
            )
            if len(pairs) >= limit:
                return pairs
    return pairs


@app.put("/api/ai/listings/{listing_id}/analysis", response_model=ListingAIAnalysisOut)
def upsert_listing_ai_analysis(
    listing_id: int,
    payload: ListingAIAnalysisWrite,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ListingAIAnalysis:
    require_crawl_secret(x_crawl_secret)
    listing = db.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    source_hash = payload.source_hash or ai_listing_source_hash(listing)
    analysis = db.scalar(select(ListingAIAnalysis).where(ListingAIAnalysis.listing_id == listing_id))
    if analysis is None:
        analysis = ListingAIAnalysis(listing_id=listing_id, source_hash=source_hash)
        db.add(analysis)
    analysis.summary = payload.summary
    analysis.features_json = payload.features_json
    analysis.red_flags_json = payload.red_flags_json
    analysis.confidence_json = payload.confidence_json
    analysis.photo_observations_json = payload.photo_observations_json
    analysis.source_hash = source_hash
    analysis.model = payload.model
    analysis.analyzed_at = datetime.utcnow()
    db.commit()
    db.refresh(analysis)
    return analysis


@app.put("/api/ai/natural-search-profiles/{profile_id}/parse", response_model=NaturalSearchProfileOut)
def update_natural_profile_parse(
    profile_id: int,
    payload: NaturalSearchProfileParseUpdate,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> NaturalSearchProfile:
    require_crawl_secret(x_crawl_secret)
    profile = db.get(NaturalSearchProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Natural search profile not found")
    profile.criteria_json = payload.criteria_json
    profile.weights_json = payload.weights_json
    profile.parsed_model = payload.parsed_model
    profile.parsed_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)
    return profile


@app.put("/api/ai/match-scores", response_model=ListingMatchScoreOut)
def upsert_listing_match_score(
    payload: ListingMatchScoreWrite,
    x_crawl_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ListingMatchScore:
    require_crawl_secret(x_crawl_secret)
    if payload.score < 0 or payload.score > 100:
        raise HTTPException(status_code=400, detail="Score must be between 0 and 100")
    if db.get(Listing, payload.listing_id) is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if db.get(NaturalSearchProfile, payload.natural_search_profile_id) is None:
        raise HTTPException(status_code=404, detail="Natural search profile not found")
    if payload.source_analysis_id is not None and db.get(ListingAIAnalysis, payload.source_analysis_id) is None:
        raise HTTPException(status_code=404, detail="Listing AI analysis not found")
    match_score = db.scalar(
        select(ListingMatchScore).where(
            ListingMatchScore.listing_id == payload.listing_id,
            ListingMatchScore.natural_search_profile_id == payload.natural_search_profile_id,
        )
    )
    if match_score is None:
        match_score = ListingMatchScore(
            listing_id=payload.listing_id,
            natural_search_profile_id=payload.natural_search_profile_id,
        )
        db.add(match_score)
    match_score.score = payload.score
    match_score.matched_reasons_json = payload.matched_reasons_json
    match_score.missing_or_uncertain_json = payload.missing_or_uncertain_json
    match_score.dealbreakers_json = payload.dealbreakers_json
    match_score.model = payload.model
    match_score.source_analysis_id = payload.source_analysis_id
    match_score.scored_at = datetime.utcnow()
    db.commit()
    db.refresh(match_score)
    return match_score


def _dedup_candidate_payload(left: Listing, right: Listing) -> dict:
    left_sources = {source.source for source in left.sources}
    right_sources = {source.source for source in right.sources}
    price_delta_ratio = None
    if left.price_eur and right.price_eur:
        price_delta_ratio = abs(left.price_eur - right.price_eur) / max(left.price_eur, right.price_eur)
    living_area_delta_m2 = None
    if left.living_area_m2 and right.living_area_m2:
        living_area_delta_m2 = abs(left.living_area_m2 - right.living_area_m2)
    return {
        "left": left,
        "right": right,
        "same_city": canonical_city_name(left.city) == canonical_city_name(right.city),
        "price_delta_ratio": price_delta_ratio,
        "living_area_delta_m2": living_area_delta_m2,
        "source_overlap": sorted(left_sources & right_sources),
    }


def listing_payload(listing: Listing) -> dict:
    return {
        "id": listing.id,
        "title": listing.title,
        "city": listing.city,
        "postal_code": listing.postal_code,
        "price_eur": listing.price_eur,
        "living_area_m2": listing.living_area_m2,
        "land_area_m2": listing.land_area_m2,
        "rooms": listing.rooms,
        "bedrooms": listing.bedrooms,
        "energy_rating": listing.energy_rating,
        "description": listing.description,
        "sources": listing.sources,
        "photos": listing.photos,
    }


def ai_listing_source_hash(listing: Listing) -> str:
    payload = {
        "title": listing.title,
        "city": listing.city,
        "postal_code": listing.postal_code,
        "price_eur": listing.price_eur,
        "living_area_m2": listing.living_area_m2,
        "land_area_m2": listing.land_area_m2,
        "rooms": listing.rooms,
        "bedrooms": listing.bedrooms,
        "energy_rating": listing.energy_rating,
        "description": listing.description,
        "sources": sorted((source.source, source.source_id, source.url) for source in listing.sources),
        "photos": sorted((photo.position, photo.url) for photo in listing.photos),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def active_search_cities(db: Session) -> list[str]:
    cities = list(
        db.scalars(select(SearchProfile.city).where(SearchProfile.enabled == True)).all()  # noqa: E712
    )
    return sorted(set(cities))


def require_crawl_access(authorization: str | None, x_crawl_secret: str | None) -> None:
    if settings.crawl_secret and x_crawl_secret == settings.crawl_secret:
        return
    if authorization and authorization.lower().startswith("bearer "):
        parse_token(authorization.split(" ", 1)[1])
        return
    raise HTTPException(status_code=401, detail="Crawl access required")


def require_crawl_secret(x_crawl_secret: str | None) -> None:
    """Stricter than require_crawl_access: no user bearer token accepted.

    Semantic dedup merge/reject are destructive (they delete a Listing) and
    are meant to be called only by the external dedup agent, not by any
    logged-in friend account -- unlike the crawl-trigger endpoints, a valid
    user token is not enough here.
    """
    if settings.crawl_secret and x_crawl_secret == settings.crawl_secret:
        return
    raise HTTPException(status_code=401, detail="Crawl secret required")


@app.get("/api/crawl-runs", response_model=list[CrawlRunOut])
def list_crawl_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CrawlRun]:
    stmt = select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(20)
    return list(db.scalars(stmt).all())


@app.get("/api/admin/users", response_model=list[AdminUserOut])
def list_admin_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[User]:
    require_admin(user)
    stmt = select(User).order_by(User.created_at)
    return list(db.scalars(stmt).all())


@app.get("/api/admin/invite-codes", response_model=list[InviteCodeOut])
def list_admin_invite_codes(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[InviteCode]:
    require_admin(user)
    stmt = select(InviteCode).order_by(InviteCode.created_at.desc())
    return list(db.scalars(stmt).all())


@app.post("/api/admin/invite-codes", response_model=InviteCodeOut)
def create_admin_invite_code(
    payload: InviteCodeCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteCode:
    require_admin(user)
    code = secrets.token_hex(4).upper()
    invite_code = InviteCode(code=code, active=True, note=payload.note)
    db.add(invite_code)
    db.commit()
    db.refresh(invite_code)
    return invite_code


@app.patch("/api/admin/invite-codes/{invite_code_id}", response_model=InviteCodeOut)
def update_admin_invite_code(
    invite_code_id: int,
    payload: InviteCodeUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteCode:
    require_admin(user)
    invite_code = db.get(InviteCode, invite_code_id)
    if invite_code is None:
        raise HTTPException(status_code=404, detail="Invite code not found")
    invite_code.active = payload.active
    db.commit()
    db.refresh(invite_code)
    return invite_code
