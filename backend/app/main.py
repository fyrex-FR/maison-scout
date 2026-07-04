from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.crawlers.demo import DemoCrawler
from app.crawlers.green_acres import GreenAcresCrawler
from app.db import Base, engine, get_db
from app.ingest import run_crawler
from app.models import CrawlRun, Listing, SearchProfile, User, UserListingState
from app.schemas import (
    AuthRequest,
    AuthResponse,
    CrawlRunOut,
    ListingOut,
    ListingStatusUpdate,
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


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/register", response_model=AuthResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    if not settings.allow_open_registration:
        raise HTTPException(status_code=403, detail="Registration disabled")
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
        city.lower()
        for city in db.scalars(
            select(SearchProfile.city).where(SearchProfile.user_id == user.id, SearchProfile.enabled == True)  # noqa: E712
        ).all()
    }
    if profile_cities:
        listings = [listing for listing in listings if listing.city.lower() in profile_cities]
    states = {
        state.listing_id: state
        for state in db.scalars(select(UserListingState).where(UserListingState.user_id == user.id)).all()
    }
    for listing in listings:
        listing.status = states.get(listing.id).status if listing.id in states else "new"
    return listings


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
    state.status = payload.status
    state.note = payload.note
    db.commit()
    stmt = (
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
    )
    listing = db.scalar(stmt)
    listing.status = state.status
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
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@app.post("/api/crawl/demo")
async def crawl_demo(db: Session = Depends(get_db)) -> dict[str, int | str]:
    run: CrawlRun = await run_crawler(db, DemoCrawler())
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/green-acres")
async def crawl_green_acres(db: Session = Depends(get_db)) -> dict[str, int | str]:
    cities = list(
        db.scalars(
            select(SearchProfile.city).where(
                SearchProfile.enabled == True,  # noqa: E712
                SearchProfile.source == "green-acres",
            )
        ).all()
    )
    run: CrawlRun = await run_crawler(db, GreenAcresCrawler.from_cities(cities))
    return {"status": run.status, "found_count": run.found_count}


@app.get("/api/crawl-runs", response_model=list[CrawlRunOut])
def list_crawl_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CrawlRun]:
    stmt = select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(20)
    return list(db.scalars(stmt).all())
