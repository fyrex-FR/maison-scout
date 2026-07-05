from dataclasses import dataclass, field


@dataclass
class CrawledListing:
    source: str
    source_id: str
    url: str
    title: str
    city: str
    postal_code: str | None = None
    price_eur: int | None = None
    living_area_m2: int | None = None
    land_area_m2: int | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    energy_rating: str | None = None
    description: str | None = None
    photos: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None


class BaseCrawler:
    source = "base"

    async def crawl(self) -> list[CrawledListing]:
        raise NotImplementedError

