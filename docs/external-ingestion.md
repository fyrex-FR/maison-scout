# External Ingestion Endpoint

## Purpose

Some listing sources (PAP, SeLoger, ...) are protected by Cloudflare /
DataDome and cannot be scraped from inside this backend's process. Instead,
an external scraper with a real browser -- e.g. **OpenClaw** -- fetches the
listings on its side and POSTs the already-extracted data to Maison Scout.

From that point on, ingestion reuses the exact same pipeline as any
in-process crawler (`ExternalBatchCrawler` -> `run_crawler` -> `upsert_listing`
for each item, see `backend/app/ingest.py`). The backend automatically
handles, with no extra logic needed by the caller:

- cross-source dedup (same city, close price, close living area)
- upsert by `(source, source_id)` -- re-ingesting the same listing updates it
  in place instead of creating a duplicate
- score computation
- photo refresh (existing photos are replaced by the new set, unless the new
  batch has none, in which case the previous photos are kept)
- price history (`PriceHistory` gets a new row whenever the price changes)
- city name normalization (`canonical_city_name`)
- a `CrawlRun` row recording the batch's outcome, same as any other crawler

This endpoint does not know anything about PAP/SeLoger/etc. specifically --
`source` is any caller-chosen string identifying where the data came from.

## Authentication

```text
X-Crawl-Secret: <server secret>
```

Same secret as the other server-to-server endpoints (`/api/crawl/*`,
`/api/semantic-dedup/*`). Unlike `/api/crawl/*`, a logged-in user's bearer
token is **not** accepted here -- only the server secret. Never log this
header or the secret value.

## Request

```http
POST /api/ingest/listings
Content-Type: application/json
X-Crawl-Secret: <server secret>

{
  "source": "pap",
  "items": [
    {
      "source_id": "pap-12345678",
      "url": "https://www.pap.fr/annonces/vente-maison-frejus-12345678",
      "title": "Maison avec piscine et vue mer",
      "city": "Frejus",
      "postal_code": "83600",
      "price_eur": 495000,
      "living_area_m2": 130,
      "land_area_m2": 600,
      "rooms": 5,
      "bedrooms": 3,
      "energy_rating": "D",
      "description": "Maison lumineuse avec piscine, proche centre-ville.",
      "photos": [
        "https://cdn.pap.fr/photos/1.jpg",
        "https://cdn.pap.fr/photos/2.jpg"
      ],
      "latitude": 43.4332,
      "longitude": 6.7358
    }
  ]
}
```

Fields:

- `source` (string, required, non-empty after trimming): identifies the
  origin of this batch (e.g. `"pap"`, `"seloger"`). Applied to every item in
  the batch -- there is no per-item source override.
- `items` (array, required): 1 to 500 listings. Empty batches and batches
  over 500 items are rejected with `400`.
- Per item, `source_id`, `url`, `title` and `city` are required strings;
  everything else is optional and defaults to `null` (or `[]` for `photos`).
  `source_id` must be stable across re-scrapes of the same ad so re-ingestion
  upserts instead of duplicating.

## Response

```json
{
  "status": "ok",
  "source": "pap",
  "found_count": 1,
  "error": null
}
```

- `status`: `"ok"` or `"error"` (mirrors `CrawlRun.status`; an unexpected
  exception during ingestion is caught and reported here rather than as a
  5xx, matching the behavior of the other crawl endpoints).
- `found_count`: number of items ingested in this batch.
- `error`: error message when `status` is `"error"`, otherwise `null`.

## Limits

- Maximum **500 items** per request. Larger batches must be split by the
  caller into multiple requests.
- No rate limiting is enforced by this endpoint beyond the secret check;
  callers should still ingest in reasonably sized batches (e.g. per crawl
  run) rather than one item at a time.

## Example

```bash
curl -X POST https://api.maison-scout.example.com/api/ingest/listings \
  -H "Content-Type: application/json" \
  -H "X-Crawl-Secret: $CRAWL_SECRET" \
  -d '{
    "source": "pap",
    "items": [
      {
        "source_id": "pap-12345678",
        "url": "https://www.pap.fr/annonces/vente-maison-frejus-12345678",
        "title": "Maison avec piscine et vue mer",
        "city": "Frejus",
        "postal_code": "83600",
        "price_eur": 495000,
        "living_area_m2": 130,
        "rooms": 5,
        "bedrooms": 3,
        "photos": ["https://cdn.pap.fr/photos/1.jpg"]
      }
    ]
  }'
```

## Design Notes

- This endpoint intentionally does not attempt to browse or bypass any
  anti-bot protection itself -- that responsibility stays entirely with the
  external scraper (OpenClaw or otherwise). The backend only ever receives
  already-extracted structured data over HTTPS.
- No new tables or model changes were introduced: ingested listings live in
  the same `Listing` / `ListingSource` / `ListingPhoto` / `PriceHistory`
  tables as listings from the in-process crawlers, and are indistinguishable
  from them except for the `source` value recorded on their `ListingSource`
  row.
