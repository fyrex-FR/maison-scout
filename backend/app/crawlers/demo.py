from app.crawlers.base import BaseCrawler, CrawledListing


class DemoCrawler(BaseCrawler):
    source = "demo"

    async def crawl(self) -> list[CrawledListing]:
        return [
            CrawledListing(
                source=self.source,
                source_id="frejus-demo-1",
                url="https://example.com/frejus-demo-1",
                title="Maison familiale avec jardin - Frejus",
                city="Frejus",
                postal_code="83600",
                price_eur=695000,
                living_area_m2=128,
                land_area_m2=620,
                rooms=5,
                bedrooms=4,
                energy_rating="C",
                description="Annonce de demonstration pour valider le pipeline Maison Scout.",
                photos=[],
            )
        ]

