from datetime import datetime

from pydantic import BaseModel, Field


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
    ai_summary: str | None = None
    red_flags: list = Field(default_factory=list)
    match_score: int | None = None
    match_reasons: list = Field(default_factory=list)
    match_missing: list = Field(default_factory=list)
    match_dealbreakers: list = Field(default_factory=list)
    active_profile_name: str | None = None
    auto_flags: list = Field(default_factory=list)
    price_dropped: bool = False
    price_change_abs: int | None = None
    price_observations: int = 0
    is_new: bool = False
    latitude: float | None = None
    longitude: float | None = None

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
    is_admin: bool = False

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


class NaturalSearchProfileCreate(BaseModel):
    name: str | None = None
    raw_prompt: str
    is_active: bool = True


class NaturalSearchProfileUpdate(BaseModel):
    name: str | None = None
    raw_prompt: str | None = None
    is_active: bool | None = None


class NaturalSearchProfileOut(BaseModel):
    id: int
    user_id: int
    name: str
    raw_prompt: str
    criteria_json: dict
    weights_json: dict
    is_active: bool
    parsed_model: str | None

    model_config = {"from_attributes": True}


class NaturalSearchProfileParseUpdate(BaseModel):
    criteria_json: dict = Field(default_factory=dict)
    weights_json: dict = Field(default_factory=dict)
    parsed_model: str | None = None


class ListingAIAnalysisOut(BaseModel):
    id: int
    listing_id: int
    summary: str | None
    features_json: dict
    red_flags_json: list
    confidence_json: dict
    photo_observations_json: list
    source_hash: str
    model: str | None

    model_config = {"from_attributes": True}


class ListingAIAnalysisWrite(BaseModel):
    summary: str | None = None
    features_json: dict = Field(default_factory=dict)
    red_flags_json: list = Field(default_factory=list)
    confidence_json: dict = Field(default_factory=dict)
    photo_observations_json: list = Field(default_factory=list)
    source_hash: str | None = None
    model: str | None = None


class AIAnalysisCandidateOut(SemanticDedupListingOut):
    source_hash: str
    current_analysis: ListingAIAnalysisOut | None = None


class ListingMatchScoreOut(BaseModel):
    id: int
    listing_id: int
    natural_search_profile_id: int
    score: int
    matched_reasons_json: list
    missing_or_uncertain_json: list
    dealbreakers_json: list
    model: str | None
    source_analysis_id: int | None

    model_config = {"from_attributes": True}


class ListingMatchScoreWrite(BaseModel):
    listing_id: int
    natural_search_profile_id: int
    score: int
    matched_reasons_json: list = Field(default_factory=list)
    missing_or_uncertain_json: list = Field(default_factory=list)
    dealbreakers_json: list = Field(default_factory=list)
    model: str | None = None
    source_analysis_id: int | None = None


class PendingMatchPairOut(BaseModel):
    listing_id: int
    natural_search_profile_id: int
    source_analysis_id: int
    source_analysis: ListingAIAnalysisOut | None = None
    natural_search_profile: NaturalSearchProfileOut | None = None


class PriceHistoryPointOut(BaseModel):
    price_eur: int
    observed_at: datetime

    model_config = {"from_attributes": True}


class AdminUserOut(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: datetime
    is_admin: bool

    model_config = {"from_attributes": True}


class InviteCodeOut(BaseModel):
    id: int
    code: str
    active: bool
    note: str | None
    used_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class InviteCodeCreate(BaseModel):
    note: str | None = None


class InviteCodeUpdate(BaseModel):
    active: bool
