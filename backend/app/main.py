from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import create_token, get_current_user, hash_password, parse_token, verify_password
from app.cities import canonical_city_name
from app.config import settings
from app.crawlers.bien_ici import BienIciCrawler
from app.crawlers.demo import DemoCrawler
from app.crawlers.green_acres import GreenAcresCrawler
from app.db import get_db
from app.ingest import run_crawler
from app.models import CrawlRun, Listing, SearchProfile, User, UserListingState
from app.schemas import (
    AuthRequest,
    AuthResponse,
    CrawlRunOut,
    ListingOut,
    ListingStatusUpdate,
    ScoreFactor,
    SearchProfileCreate,
    SearchProfileOut,
    UserOut,
)

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/register", response_model=AuthResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    if not settings.allow_open_registration:
        raise HTTPException(status_code=403, detail="Registration disabled")
    invite_codes = settings.invite_code_set
    if invite_codes and (payload.invite_code or "").strip() not in invite_codes:
        raise HTTPException(status_code=403, detail="Invalid invite code")
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
    db.commit()
    db.refresh(user)
    return AuthResponse(token=create_token(user), user=user)


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return AuthResponse(token=create_token(user), user=user)


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@app.get("/api/listings", response_model=list[ListingOut])
def list_listings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Listing]:
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
    profiles = list(
        db.scalars(select(SearchProfile).where(SearchProfile.user_id == user.id, SearchProfile.enabled == True)).all()  # noqa: E712
    )
    states = {
        state.listing_id: state
        for state in db.scalars(select(UserListingState).where(UserListingState.user_id == user.id)).all()
    }
    for listing in listings:
        state = states.get(listing.id)
        listing.status = state.status if state else "new"
        listing.note = state.note if state else None
        score, breakdown = score_for_user(listing, profiles)
        listing.score = score
        listing.score_breakdown = breakdown
    return listings


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
    return {
        "status": "ok" if all(run["status"] == "ok" for run in runs) else "partial",
        "found_count": sum(int(run["found_count"]) for run in runs),
        "runs": runs,
    }


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


@app.get("/api/crawl-runs", response_model=list[CrawlRunOut])
def list_crawl_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CrawlRun]:
    stmt = select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(20)
    return list(db.scalars(stmt).all())
