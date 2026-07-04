from pydantic import BaseModel


class ListingSourceOut(BaseModel):
    source: str
    url: str


class ListingPhotoOut(BaseModel):
    url: str
    position: int


class ListingOut(BaseModel):
    id: int
    title: str
    city: str
    postal_code: str | None
    price_eur: int | None
    living_area_m2: int | None
    land_area_m2: int | None
    rooms: int | None
    bedrooms: int | None
    energy_rating: str | None
    description: str | None
    score: int | None
    status: str
    sources: list[ListingSourceOut]
    photos: list[ListingPhotoOut]

    model_config = {"from_attributes": True}


class ListingStatusUpdate(BaseModel):
    status: str


class CrawlRunOut(BaseModel):
    id: int
    source: str
    status: str
    found_count: int
    error: str | None

    model_config = {"from_attributes": True}
