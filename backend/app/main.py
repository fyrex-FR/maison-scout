from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.crawlers.demo import DemoCrawler
from app.crawlers.green_acres import GreenAcresCrawler
from app.db import Base, engine, get_db
from app.ingest import run_crawler
from app.models import CrawlRun, Listing
from app.schemas import CrawlRunOut, ListingOut, ListingStatusUpdate

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


@app.get("/api/listings", response_model=list[ListingOut])
def list_listings(db: Session = Depends(get_db)) -> list[Listing]:
    stmt = (
        select(Listing)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
        .order_by(Listing.score.desc().nullslast(), Listing.updated_at.desc())
    )
    return list(db.scalars(stmt).all())


@app.patch("/api/listings/{listing_id}/status", response_model=ListingOut)
def update_listing_status(listing_id: int, payload: ListingStatusUpdate, db: Session = Depends(get_db)) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.status = payload.status
    db.commit()
    stmt = (
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.sources), selectinload(Listing.photos))
    )
    return db.scalar(stmt)


@app.post("/api/crawl/demo")
async def crawl_demo(db: Session = Depends(get_db)) -> dict[str, int | str]:
    run: CrawlRun = await run_crawler(db, DemoCrawler())
    return {"status": run.status, "found_count": run.found_count}


@app.post("/api/crawl/green-acres")
async def crawl_green_acres(db: Session = Depends(get_db)) -> dict[str, int | str]:
    run: CrawlRun = await run_crawler(db, GreenAcresCrawler())
    return {"status": run.status, "found_count": run.found_count}


@app.get("/api/crawl-runs", response_model=list[CrawlRunOut])
def list_crawl_runs(db: Session = Depends(get_db)) -> list[CrawlRun]:
    stmt = select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(20)
    return list(db.scalars(stmt).all())
