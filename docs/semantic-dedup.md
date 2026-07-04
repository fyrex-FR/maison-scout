# Semantic Dedup Runbook

Maison Scout keeps the deterministic crawler and numeric ingest dedup as the
first line of defense. Semantic dedup is a second pass for cross-source
duplicates that differ too much in price or living area for the conservative
numeric heuristic.

The backend deliberately does **not** call OpenAI or store an AI key. An
external agent fetches candidate pairs, compares title/description/photos with
its own model access, then calls either merge or reject.

## Endpoints

All endpoints are internal and protected like crawler endpoints:

```text
Authorization: Bearer <user-token>
```

or:

```text
X-Crawl-Secret: <server secret>
```

### Fetch candidate pairs

```http
GET /api/semantic-dedup/candidates?days=14&limit=50
```

Returns same-city, cross-source listing pairs that have not already been
reviewed in `semantic_dedup_decisions`.

The response includes both listing payloads with sources/photos plus numeric
hints (`price_delta_ratio`, `living_area_delta_m2`). The external agent should
compare visible image content, title, and description before deciding.

### Merge confirmed duplicates

```http
POST /api/semantic-dedup/merge
Content-Type: application/json

{
  "target_listing_id": 123,
  "duplicate_listing_id": 456,
  "confidence": 93,
  "reason": "Same facade, pool, terrace and agency text.",
  "model": "gpt-4.1"
}
```

Fusion behavior:

- keeps `target_listing_id`
- moves all `ListingSource` rows from the duplicate
- appends non-duplicate photos
- moves price history
- merges per-user notes/statuses
- remaps comparison items
- records a `semantic_dedup_decisions` row
- deletes the duplicate `Listing`

### Reject non-duplicates

```http
POST /api/semantic-dedup/reject
Content-Type: application/json

{
  "left_listing_id": 123,
  "right_listing_id": 456,
  "confidence": 12,
  "reason": "Different exterior and different street context.",
  "model": "gpt-4.1"
}
```

This only records the decision, so the pair is not presented again.

## Design Notes

- The existing deterministic ingest dedup remains unchanged and still handles
  obvious same-city, close-price, close-surface duplicates.
- Semantic dedup never runs inside the scraper cron by itself. Scheduling and
  model calls belong to the external agent.
- Merge deletes only the duplicate `Listing` row after moving dependent data.
  Decision rows store plain integer ids, not foreign keys, so historical merge
  records remain readable after deletion.
- Confidence is an integer conventionally interpreted as 0-100, but the
  backend does not enforce a threshold. The external agent owns that policy.
