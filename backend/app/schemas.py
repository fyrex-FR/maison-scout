from pydantic import BaseModel


class ListingSourceOut(BaseModel):
    source: str
    url: str


class ListingPhotoOut(BaseModel):
    url: str
    position: int


class ScoreFactor(BaseModel):
    label: str
    delta: int


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
    note: str | None = None
    score_breakdown: list[ScoreFactor] | None = None
    sources: list[ListingSourceOut]
    photos: list[ListingPhotoOut]

    model_config = {"from_attributes": True}


class SemanticDedupListingOut(BaseModel):
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
    sources: list[ListingSourceOut]
    photos: list[ListingPhotoOut]

    model_config = {"from_attributes": True}


class SemanticDedupCandidateOut(BaseModel):
    left: SemanticDedupListingOut
    right: SemanticDedupListingOut
    same_city: bool
    price_delta_ratio: float | None
    living_area_delta_m2: int | None
    source_overlap: list[str]


class SemanticDedupDecisionRequest(BaseModel):
    left_listing_id: int | None = None
    right_listing_id: int | None = None
    target_listing_id: int | None = None
    duplicate_listing_id: int | None = None
    confidence: int | None = None
    reason: str | None = None
    model: str | None = None


class SemanticDedupDecisionOut(BaseModel):
    id: int
    left_listing_id: int
    right_listing_id: int
    status: str
    confidence: int | None
    reason: str | None
    model: str | None

    model_config = {"from_attributes": True}


class ListingStatusUpdate(BaseModel):
    status: str | None = None
    note: str | None = None


class CrawlRunOut(BaseModel):
    id: int
    source: str
    status: str
    found_count: int
    error: str | None

    model_config = {"from_attributes": True}


class AuthRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    invite_code: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class SearchProfileCreate(BaseModel):
    name: str | None = None
    city: str
    source: str = "green-acres"
    max_price_eur: int | None = None
    min_living_area_m2: int | None = None
    min_land_area_m2: int | None = None
    min_bedrooms: int | None = None


class SearchProfileOut(BaseModel):
    id: int
    name: str
    city: str
    source: str
    enabled: bool
    max_price_eur: int | None
    min_living_area_m2: int | None
    min_land_area_m2: int | None
    min_bedrooms: int | None

    model_config = {"from_attributes": True}
