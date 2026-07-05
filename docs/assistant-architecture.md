# Maison Scout Assistant Architecture

Maison Scout is not intended to stay a simple listing aggregator. The target
product is a personal real-estate search assistant: each user describes what
they are looking for in natural language, and the app ranks houses according
to that user's preferences with clear explanations.

This document records the architecture decision before implementation so future
work does not mix scraping, AI analysis, deduplication, and user matching into
one tangled feature.

## Product Goal

Users should be able to write requirements like:

```text
I want a house with at least 4 bedrooms, air conditioning, a pool, and direct
access from the living areas to the garden or pool without stairs.
```

Maison Scout should then show matching listings for that user, ranked and
explained:

- why the listing matches
- what is missing
- what is uncertain
- which points should be verified in the source listing or during a visit

Classic filters still matter. Natural-language preferences refine and rank the
results; they do not replace price/city/surface/bedroom filters.

## Core Separation

### 1. Crawlers Collect Data

The source crawlers remain deterministic and cheap:

- Green-Acres
- Bien'ici
- future portals if added

They should collect raw and normalized listing data: title, description, price,
surface, location, photos, source URL, source id, and any source-provided
attributes.

Crawlers must not call AI models and must not decide whether a listing is a
good match for a user.

### 2. Backend Stores and Validates

The FastAPI backend is the source of truth for:

- users
- search cities and classic criteria
- listings and source records
- listing photos and price history
- notes, statuses, and comparisons
- AI analysis results
- per-user natural search profiles
- per-profile match scores
- semantic dedup decisions

The backend should expose explicit endpoints for reading/writing this state,
validate inputs, and keep an audit trail for AI-assisted decisions.

The backend should not store an OpenAI key or call AI providers directly for
this project. It should receive structured results from the external OpenClaw
worker.

### 3. OpenClaw/Jarvis Runs AI Work

OpenClaw is the external worker and AI runtime. It can:

- fetch listings that need analysis
- inspect title, description, and selected photos
- extract structured property attributes
- parse each user's natural-language prompt into criteria
- compute per-user listing match scores
- submit structured results back to the backend
- run semantic dedup checks separately from matching

This keeps secrets out of the public app, keeps the backend simple, and is
appropriate for a personal project with limited volume.

## Global Listing Analysis

A listing should be analyzed once, independent of any user. The property does
not change depending on who is looking at it.

Planned table:

```text
listing_ai_analysis
```

Suggested fields:

- `id`
- `listing_id`
- `summary`
- `features_json`
- `red_flags_json`
- `confidence_json`
- `photo_observations_json`
- `source_hash`
- `model`
- `analyzed_at`
- `created_at`
- `updated_at`

Example extracted features:

- probable bedroom count
- pool present / absent / uncertain
- air conditioning mentioned / not mentioned / uncertain
- single-storey or multi-level hints
- direct living-room/kitchen access to terrace, garden, or pool
- stairs between living areas and outdoor spaces
- outdoor usability
- visible renovation needs
- privacy or overlook concerns
- confidence per feature

`source_hash` should reflect the source data used for analysis, such as title,
description, photo URLs, and key normalized fields. If the hash has not changed,
the worker should avoid re-analyzing the listing.

## Per-User Natural Search Profiles

Each user can have their own natural-language preference profile. The same
house can be an excellent match for one user and a poor match for another.

Planned table:

```text
natural_search_profiles
```

Suggested fields:

- `id`
- `user_id`
- `name`
- `raw_prompt`
- `criteria_json`
- `weights_json`
- `is_active`
- `parsed_model`
- `parsed_at`
- `created_at`
- `updated_at`

Examples:

- "4 bedrooms minimum, pool, air conditioning, direct access to the garden from
  living spaces, no stairs to the pool."
- "Budget-conscious, renovation work is acceptable, large plot preferred, pool
  optional."
- "Strong resale potential, good outdoor space, not too far from the coast."

Classic `search_profiles` remain responsible for city and numeric constraints
such as max price, minimum living area, minimum land area, and bedroom count.
`natural_search_profiles` are for subjective and semantic preferences.

## Per-Profile Match Scores

Matching is user-specific. Scores must be attached to a natural search profile,
not just to a listing.

Planned table:

```text
listing_match_scores
```

Suggested fields:

- `id`
- `listing_id`
- `natural_search_profile_id`
- `score`
- `matched_reasons_json`
- `missing_or_uncertain_json`
- `dealbreakers_json`
- `model`
- `source_analysis_id`
- `scored_at`
- `created_at`
- `updated_at`

The score should be explainable. The UI should be able to show:

- positive reasons
- missing requirements
- uncertain requirements
- possible dealbreakers
- "verify this during a visit" notes

## Workflow

### New or Updated Listing

1. Crawler ingests the listing.
2. Deterministic ingest dedup runs as it does today.
3. Backend exposes the listing as needing AI analysis if no fresh
   `listing_ai_analysis` exists for its current `source_hash`.
4. OpenClaw analyzes the listing text/photos once.
5. OpenClaw writes `listing_ai_analysis`.
6. OpenClaw computes `listing_match_scores` for active natural profiles whose
   classic search cities/criteria include the listing.

### User Updates Their Prompt

1. User edits their natural-language profile.
2. Backend stores the raw prompt.
3. OpenClaw parses it into criteria and weights.
4. OpenClaw recomputes match scores for that user's profile only.
5. Existing global listing analyses are reused; photo analysis should not be
   repeated just because one user changed their prompt.

### Periodic Worker Cadence

OpenClaw jobs should stay separate:

- crawl remains on the existing backend/server cron
- semantic dedup worker runs after crawl and only decides same-property pairs
- listing analysis worker processes listings missing fresh analysis
- matching worker updates per-profile scores

These jobs may run on similar schedules, but they should remain logically
separate.

## Security Model

Public user endpoints:

- authentication
- user's listings
- notes/statuses
- comparisons
- classic search profiles
- natural search profiles

Internal endpoints:

- crawl triggers
- semantic dedup candidates/merge/reject
- AI analysis writeback
- match score writeback
- worker queues/status

Internal endpoints should use the server secret:

```text
X-Crawl-Secret: <server secret>
```

Do not accept normal user bearer tokens for destructive or worker-only
operations. Do not log the secret. Store AI decisions with model, confidence,
reason, and timestamps where applicable.

## Cost Control

The project is personal and low volume, so OpenClaw-driven AI work is
reasonable if it stays bounded.

Rules:

- analyze only new or changed listings
- cache analysis by `source_hash`
- cap the number of photos sent to vision models
- prefer text-only analysis first when enough information is present
- use vision for criteria that require photos, such as exterior access,
  stairs, pool context, layout hints, and visible condition
- reuse global listing analysis for all users
- recompute user scores without redoing photo analysis

## Relationship to Semantic Dedup

Semantic dedup is already a separate data hygiene feature. It answers:

```text
Are these two source listings the same physical property?
```

Natural search matching answers:

```text
How well does this property match this user's preferences?
```

These must not be merged into one workflow. Dedup can remove duplicate
properties from the database, but it should not decide user preference scores.

See also:

- `docs/semantic-dedup.md`

## Implementation Status

Already in place and tested:

- deterministic crawlers and ingest
- classic per-user search profiles
- notes/statuses/comparisons
- deterministic ingest dedup
- semantic dedup endpoints and audit table
- OpenClaw semantic dedup job
- assistant storage foundation:
  - `listing_ai_analysis`
  - `natural_search_profiles`
  - `listing_match_scores`
- user-facing CRUD endpoints for natural-language search profiles
  (`/api/natural-search-profiles`, create/list/patch/delete), including the
  "editing `raw_prompt` resets `criteria_json`/`weights_json`/`parsed_model`"
  behavior that marks a profile as needing re-parsing
- internal `X-Crawl-Secret` endpoints for the assistant worker:
  - `GET /api/ai/listings/pending-analysis`
  - `PUT /api/ai/listings/{listing_id}/analysis`
  - `PUT /api/ai/natural-search-profiles/{profile_id}/parse`
  - `PUT /api/ai/match-scores`

In progress (other agents, in parallel):

- exposing `listing_ai_analysis` / `listing_match_scores` data through the
  public `/api/listings` response (`ai_summary`, `red_flags`, `match_score`
  fields already exist on `ListingOut` and are being wired up)
- frontend UI for editing natural-language prompts and displaying
  personalized, explained rankings

Planned next:

1. Build the external OpenClaw worker that actually calls an AI model to
   produce listing analyses, parse prompts, and compute match scores against
   the internal endpoints above. See `docs/openclaw-assistant-worker.md` for
   the full build spec, including two known backend gaps (no internal
   "profiles pending parse" listing endpoint, no internal "scoring scope"
   endpoint) that block Jobs B/C from running fully unattended until
   addressed.
