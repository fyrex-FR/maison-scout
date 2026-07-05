# OpenClaw Assistant Worker — Build Spec

This document is the spec/prompt for building the **external OpenClaw (Jarvis)
worker** that powers the Maison Scout AI assistant features: listing analysis,
natural-language prompt parsing, and per-user match scoring.

It is written to be handed to an OpenClaw/Jarvis coding agent with no other
context. If you are that agent: read this file fully before writing any code.
The backend and frontend of Maison Scout are being worked on by other agents in
parallel — do not modify anything in the `maison-scout` repository. This
worker lives in its own codebase/runtime (OpenClaw), and talks to Maison Scout
only over HTTP using the endpoints below.

See also, for background and for the sibling job this worker must stay
separate from:

- `docs/assistant-architecture.md` — overall product/architecture decision
- `docs/semantic-dedup.md` — the existing semantic dedup job (same pattern,
  different job, do not merge the two)

## 1. Role and Separation Principle

Maison Scout splits real-estate search into three layers that must never
collapse into each other:

1. **Crawlers (deterministic, inside Maison Scout backend/cron)** — scrape
   Green-Acres, Bien'ici, etc. Collect raw/normalized listing data: title,
   description, price, surface, location, photos, source URL/id. Crawlers
   never call an AI model and never judge whether a listing suits a user.
2. **Backend (FastAPI, inside Maison Scout)** — source of truth. Stores
   listings, photos, price history, notes/statuses, comparisons, semantic
   dedup decisions, AI analysis results, natural-language search profiles,
   and match scores. Validates inputs. Exposes explicit internal endpoints for
   this worker to read pending work and write back structured AI results.
   **The backend does not hold an OpenAI key and does not call any AI model
   itself.**
3. **OpenClaw/Jarvis (this worker, external)** — the only place in the whole
   system allowed to call an LLM. It uses **its own ChatGPT/OpenAI
   integration** (already configured in OpenClaw) to read listings, parse
   prompts, and score matches, then pushes structured JSON results back to the
   Maison Scout backend over HTTP.

Hard rule: **no OpenAI key, no direct model call, and no AI-provider SDK ever
get added to the `maison-scout` repository.** All model calls happen inside
OpenClaw using OpenClaw's own ChatGPT integration. This worker only speaks
plain HTTPS + JSON to the Maison Scout backend, authenticated with a shared
secret header.

## 2. Authentication

Every endpoint below is an **internal worker endpoint**. It is authenticated
with a static shared secret, never with a user's login token:

```text
X-Crawl-Secret: <server secret>
```

Rules:

- Read the secret value from OpenClaw's own secret storage/config for this
  job. Never hardcode it in source, never print/log it (not even at debug
  level), and never include it in error messages that get logged.
- Never send a user bearer/session token to any `/api/ai/*` or
  `/api/semantic-dedup/*` endpoint. These endpoints intentionally reject that
  pattern — they are worker-only, some of them are destructive or write
  irreversible AI-authored data.
- If a request returns `401`/`403`, stop and surface a clear error — do not
  retry with different credentials, do not fall back to skipping auth.

## 3. Base URL and Endpoint Reference

Base URL: the Maison Scout backend's deployed origin (get it from OpenClaw
config for this job — do not hardcode a specific host in code).

### 3.1 Listing analysis endpoints

**Fetch listings needing (re)analysis**

```http
GET /api/ai/listings/pending-analysis?limit=25
X-Crawl-Secret: <server secret>
```

Returns up to `limit` (max 100) listings whose stored `listing_ai_analysis`
is missing or stale. Staleness is decided by the backend by comparing a
`source_hash` it computes from the listing's current title, city, postal
code, price, living/land area, rooms, bedrooms, energy rating, description,
sources, and photos. If any of that changed since the last analysis, the
listing reappears here.

Each item shape (`AIAnalysisCandidateOut`):

```json
{
  "id": 123,
  "title": "...",
  "city": "...",
  "postal_code": "...",
  "price_eur": 450000,
  "living_area_m2": 140,
  "land_area_m2": 800,
  "rooms": 6,
  "bedrooms": 4,
  "energy_rating": "D",
  "description": "...",
  "sources": [{"source": "green-acres", "url": "..."}],
  "photos": [{"url": "...", "position": 0}],
  "source_hash": "…64 hex chars…",
  "current_analysis": {
    "id": 55,
    "listing_id": 123,
    "summary": "...",
    "features_json": {...},
    "red_flags_json": [...],
    "confidence_json": {...},
    "photo_observations_json": [...],
    "source_hash": "…previous hash, will differ from top-level source_hash…",
    "model": "gpt-4.1"
  }
}
```

`current_analysis` is `null` if the listing has never been analyzed. If it is
present, its `source_hash` is the *old* value — that is exactly why this
listing is in the pending list (hash mismatch), or it's a first-time entry.

**Write back a listing's analysis**

```http
PUT /api/ai/listings/{listing_id}/analysis
X-Crawl-Secret: <server secret>
Content-Type: application/json

{
  "summary": "4-bedroom villa with pool, single storey, direct garden access from living room.",
  "features_json": {
    "bedrooms_probable": 4,
    "pool": "present",
    "air_conditioning": "uncertain",
    "single_storey": true,
    "living_room_to_garden_direct_access": "present",
    "living_room_to_pool_direct_access": "uncertain",
    "stairs_between_living_and_outdoor": "absent",
    "renovation_needed": "minor",
    "overlook_privacy_concern": "low"
  },
  "red_flags_json": [
    {"code": "low_photo_count", "detail": "Only 2 photos provided."}
  ],
  "confidence_json": {
    "pool": 0.9,
    "air_conditioning": 0.3,
    "single_storey": 0.8
  },
  "photo_observations_json": [
    {"photo_url": "...", "observations": ["visible pool", "terrace with direct living-room access"]}
  ],
  "source_hash": "…the source_hash you received from pending-analysis for this listing…",
  "model": "gpt-4.1"
}
```

This is an upsert (one row per listing; unique on `listing_id`). Always send
back the exact `source_hash` you were given for that listing in this batch —
this is what marks the analysis as fresh and removes the listing from
`pending-analysis` until it changes again. If you omit `source_hash`, the
backend recomputes it itself from the current listing state, but you should
not rely on that: fetch and analyze in the same pass so the hash you write
back matches what you actually analyzed.

### 3.2 Natural-language profile parsing endpoint

```http
PUT /api/ai/natural-search-profiles/{profile_id}/parse
X-Crawl-Secret: <server secret>
Content-Type: application/json

{
  "criteria_json": {
    "bedrooms_min": 4,
    "pool": "required",
    "air_conditioning": "required",
    "living_room_direct_access_to": ["garden", "pool"],
    "stairs_to_outdoor": "disallowed"
  },
  "weights_json": {
    "pool": 1.0,
    "air_conditioning": 0.6,
    "living_room_direct_access_to": 0.9,
    "stairs_to_outdoor": 0.9
  },
  "parsed_model": "gpt-4.1"
}
```

This is an upsert of the parse result for one `natural_search_profiles` row.
To discover which profiles still need parsing, use
`GET /api/ai/natural-search-profiles/pending-parse?limit=50` (see section 6).

### 3.3 Match score endpoint

```http
PUT /api/ai/match-scores
X-Crawl-Secret: <server secret>
Content-Type: application/json

{
  "listing_id": 123,
  "natural_search_profile_id": 7,
  "score": 82,
  "matched_reasons_json": [
    "4 bedrooms as required",
    "Pool present",
    "Living room opens directly onto garden"
  ],
  "missing_or_uncertain_json": [
    "Air conditioning not mentioned in listing text or visible in photos"
  ],
  "dealbreakers_json": [],
  "model": "gpt-4.1",
  "source_analysis_id": 55
}
```

This is an upsert, unique on `(listing_id, natural_search_profile_id)`.
`score` must be an integer `0..100` (the backend rejects anything outside that
range with `400`). `source_analysis_id` should be the `id` of the
`listing_ai_analysis` row you used as your source of truth for this scoring
pass (the `current_analysis.id`, or the `id` returned by the analysis PUT) —
this lets the backend/UI show which analysis a score is explained by, and
lets you skip re-scoring if neither the analysis nor the profile changed.

### 3.4 Not part of this worker

Semantic dedup (`/api/semantic-dedup/candidates`, `/merge`, `/reject`) is a
**separate job** with its own doc (`docs/semantic-dedup.md`). It answers "are
these two source listings the same physical property", not "does this
property match this user". Do not fold dedup logic into this worker's jobs,
and do not let this worker call the merge/reject endpoints.

## 4. The Three Jobs

Build these as three logically separate jobs (separate functions/entry points
at minimum; separate scheduled runs if your runner supports it). They can
share HTTP client/auth code, but keep their responsibilities isolated so a
change to one never silently affects another.

### Job A — Listing Analysis (global, user-independent)

**When it runs:** after each crawl cycle (crawl runs on an existing ~6h cron
inside Maison Scout — see `docs/assistant-architecture.md`). Run this job
shortly after crawl finishes, or on its own schedule no more often than the
crawl cadence, whichever is simpler to operate. This job never needs to run
more often than listings actually change.

**What it reads:** `GET /api/ai/listings/pending-analysis?limit=25`, paged
(call again with the same limit until it returns fewer than `limit` items or
an empty list — that means the backlog is drained for this run).

**What it does, per listing:**

1. Build a text prompt from `title`, `description`, `city`, `postal_code`,
   `price_eur`, `living_area_m2`, `land_area_m2`, `rooms`, `bedrooms`,
   `energy_rating`.
2. Decide if photos are needed (see Cost Control, section 5). If yes, select
   a capped, deduplicated subset of `photos` (ordered by `position`) and pass
   them to the vision-capable ChatGPT model alongside the text.
3. Ask the model to extract, at minimum:
   - probable bedroom count (can differ from the source's stated `bedrooms`)
   - pool: `present` / `absent` / `uncertain`
   - air conditioning: `present` / `absent` / `uncertain`
   - single-storey vs multi-level hints
   - direct access from living room/kitchen to garden, terrace, or pool
   - presence of stairs between living areas and outdoor spaces
   - outdoor space usability (terrace, garden size/shape hints)
   - visible renovation needs / condition
   - overlook/privacy concerns (vis-à-vis)
   - a short natural-language `summary` of the property
   - a confidence score (0–1) per extracted feature
   - `red_flags`: e.g. energy rating F/G, no photos at all, price per m²
     far outside the local norm, missing surface data, description far
     shorter than typical, contradictions between stated and observed
     features
   - `photo_observations`: per-photo short notes on what was seen, keyed by
     photo URL, so the UI can show "why" for a given picture
4. Map the model's output into the exact request body shape in section 3.1
   (`features_json`, `red_flags_json`, `confidence_json`,
   `photo_observations_json`).
5. `PUT /api/ai/listings/{listing_id}/analysis` with that body and the
   `source_hash` you received for this listing in this batch.
6. Record the `id` returned in the response (the `listing_ai_analysis.id`) —
   Job C needs it as `source_analysis_id`.

**Failure handling:** if the model call fails or returns unparseable output
for a listing, skip that listing (leave it pending — it will reappear in the
next `pending-analysis` call since nothing was written) and continue with the
rest of the batch. Do not let one bad listing abort the whole run.

### Job B — Natural Prompt Parsing (per profile, triggered by user edits)

**When it runs:** whenever a user creates or edits a `natural_search_profiles`
row. The backend already marks "needs parsing" implicitly: on create, and on
any `raw_prompt` update, it resets `criteria_json`/`weights_json` to `{}` and
`parsed_model`/`parsed_at` to `null`. A profile needing parsing is therefore
one where `criteria_json == {}` (or `parsed_model is null`).

**What it reads/writes:** poll `GET /api/ai/natural-search-profiles/pending-parse`
(see section 6) to get the active, not-yet-parsed profiles to work on, then
write each result back via `PUT /api/ai/natural-search-profiles/{id}/parse`.

**What it does, per profile:**

1. Take `raw_prompt` (free-form text such as "4 bedrooms minimum, pool, air
   conditioning, direct access to the garden from living spaces, no stairs to
   the pool").
2. Ask the model to produce a structured `criteria_json` (hard requirements
   and preferences, using the same feature vocabulary as Job A's
   `features_json` so the two are comparable — e.g. `pool`, `air_conditioning`,
   `bedrooms_min`, `living_room_direct_access_to`, `stairs_to_outdoor`) and a
   `weights_json` giving each criterion a relative importance (a simple 0–1
   scale is enough; do not over-engineer this).
3. `PUT /api/ai/natural-search-profiles/{profile_id}/parse` with
   `criteria_json`, `weights_json`, `parsed_model`.
4. Trigger or queue Job C for this profile against all listings currently
   in scope for that user (see Job C).

### Job C — Match Scoring (per user/profile × listing)

**When it runs:** after Job A produces/updates a listing's global analysis
(score that listing against all active profiles whose classic search
criteria — city, price, etc., defined in `search_profiles`, out of scope for
this worker — include it), and after Job B (re)parses a profile (score that
profile against all currently-in-scope listings). In steady state this
follows the same post-crawl cadence as Job A.

**What it reads:** the global analysis produced by Job A (`features_json`,
`red_flags_json`, `confidence_json`, `summary`, and its `id` as
`source_analysis_id`) and the parsed profile from Job B (`criteria_json`,
`weights_json`). Do not re-run vision/photo analysis here — that is exactly
the case Job A already solved once per listing.

**What it does, per (listing, profile) pair in scope:**

1. Compare `criteria_json`/`weights_json` against the listing's
   `features_json`/`confidence_json`.
2. Compute a `score` in `0..100`.
3. Produce `matched_reasons_json` (why it scores well),
   `missing_or_uncertain_json` (requirements not met or not confidently
   verifiable — e.g. "air conditioning uncertain from listing"), and
   `dealbreakers_json` (hard requirement clearly violated, e.g. user requires
   pool and analysis says `pool: absent`).
4. `PUT /api/ai/match-scores` with `listing_id`, `natural_search_profile_id`,
   `score`, the three explanation arrays, `model`, and `source_analysis_id`
   set to the `listing_ai_analysis.id` used in step 1.

**Reuse rule:** if neither the listing's analysis (`source_analysis_id`
target unchanged) nor the profile's `criteria_json`/`weights_json` changed
since the last recorded score for that pair, skip recomputation. Only two
events should ever trigger a recompute for a given pair: the listing's
analysis changed, or the profile's parse changed.

## 5. Cost Control

This is a personal, low-volume project. AI spend must stay bounded:

- Only analyze listings that `pending-analysis` actually returns — never
  re-scan the whole listing table proactively.
- Cache by `source_hash`: never call the model for a listing whose current
  `source_hash` matches what's already stored.
- Cap photos sent to vision per listing (pick a small fixed number, e.g. 4–6,
  favoring lower `position` values — those are usually the lead/cover photos).
- Prefer text-only analysis when the description already gives high-confidence
  answers; only reach for vision when a criterion genuinely needs visual
  confirmation (pool, stairs/level layout, direct outdoor access, visible
  condition, overlook/vis-à-vis).
- Job A's output is reused by Job C across every user/profile — never
  re-derive listing features per user, and never re-run photo analysis just
  because a user edited their prompt (that only triggers Job B + a Job C pass
  that reuses the existing analysis).
- Batch and paginate `pending-analysis` instead of pulling more than needed.

## 6. Discovery Endpoints (all three jobs are fully autonomous)

Every job can discover its own work over HTTP with `X-Crawl-Secret`; no direct
database access is needed. The two discovery endpoints below complement
`GET /api/ai/listings/pending-analysis` (Job A):

1. **Profiles needing parsing (Job B):**
   ```http
   GET /api/ai/natural-search-profiles/pending-parse?limit=50
   X-Crawl-Secret: <server secret>
   ```
   Returns `natural_search_profiles` that are `is_active == true` and not yet
   parsed (`parsed_at IS NULL`), oldest first, `limit` clamped 1..200. Response
   shape matches the user-facing profile object (`id`, `user_id`, `name`,
   `raw_prompt`, `criteria_json`, `weights_json`, `is_active`, `parsed_model`).

2. **Pairs needing scoring (Job C):**
   ```http
   GET /api/ai/pending-match-scores?limit=100
   X-Crawl-Secret: <server secret>
   ```
   Returns the `(listing, profile)` pairs to score, already filtered by the
   backend so you only get real work:
   ```json
   [{ "listing_id": 123, "natural_search_profile_id": 7, "source_analysis_id": 42 }]
   ```
   A pair is included only when: the profile is active **and** parsed, the
   listing has a `listing_ai_analysis`, the listing passes the profile owner's
   classic `search_profiles` (city/price/surface/bedrooms), and either no score
   exists yet or the existing score's `source_analysis_id` is stale (the listing
   was re-analyzed). `limit` is clamped 1..500. Use the returned
   `source_analysis_id` when writing the score back.

Both endpoints require `X-Crawl-Secret` and reject user tokens (401 otherwise).

## 7. Security Checklist

- [ ] `X-Crawl-Secret` read from secret storage, never hardcoded, never logged.
- [ ] No user bearer/session token ever sent to `/api/ai/*` endpoints.
- [ ] No OpenAI/model API key, SDK, or call added anywhere in the
      `maison-scout` repository — all model calls stay inside OpenClaw.
- [ ] Every write includes `model` (the model name/version used) so results
      are auditable.
- [ ] Every analysis/score write is traceable to a timestamp
      (`analyzed_at`/`scored_at`, set server-side automatically — you don't
      need to send it) and, for scores, to a `source_analysis_id`.
- [ ] Errors from one listing/profile/pair never abort a whole batch run.
- [ ] Nothing in this worker calls `/api/semantic-dedup/*`.

## 8. Cadence Summary

```text
crawl (existing, ~6h cron, inside Maison Scout)
   -> Job A: listing analysis (drains pending-analysis)
        -> Job C: match scoring for listings just (re)analyzed
user edits/creates a natural_search_profile (event-driven)
   -> Job B: parse the prompt
        -> Job C: match scoring for that profile against in-scope listings
```

Job A and the "Job C after Job A" pass should run together right after crawl.
Job B and its Job C pass are event-driven and independent of the crawl clock.

## 9. Definition of Done

- [ ] Job A pulls `pending-analysis`, analyzes text (+ capped photos when
      needed), and writes back via `PUT /api/ai/listings/{id}/analysis` with
      the exact `source_hash` it was given, for every listing in the pending
      batch, looping until the batch is drained.
- [ ] A listing whose `source_hash` has not changed is never re-analyzed
      (verified by running Job A twice in a row with no listing changes in
      between and observing zero model calls on the second run).
- [ ] Job B parses a profile's `raw_prompt` into `criteria_json`/
      `weights_json` and writes it back via
      `PUT /api/ai/natural-search-profiles/{id}/parse`, using the same feature
      vocabulary as Job A's `features_json`.
- [ ] Job C computes a `0..100` score with `matched_reasons_json`,
      `missing_or_uncertain_json`, `dealbreakers_json`, and a correct
      `source_analysis_id`, and writes it via `PUT /api/ai/match-scores`.
- [ ] Job C never re-runs photo/vision analysis; it only reuses Job A's
      stored `listing_ai_analysis`.
- [ ] Job C skips recomputation when neither the relevant analysis nor the
      relevant profile parse changed since the last stored score.
- [ ] All three jobs authenticate solely with `X-Crawl-Secret`; none accept or
      send a user token; the secret is never logged.
- [ ] No OpenAI key or AI SDK call exists anywhere under the `maison-scout`
      repository as a result of this work — confirmed by grepping the repo
      for the model provider's SDK/key names and finding nothing.
- [ ] A single bad/unparseable listing, profile, or pair does not stop the
      rest of a batch from being processed.
- [ ] Job B discovers its work via `GET /api/ai/natural-search-profiles/pending-parse`
      and Job C via `GET /api/ai/pending-match-scores` — no direct database
      connection and no guessed endpoints.
