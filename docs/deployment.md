# Deployment

## Backend and database on Coolify

Create a PostgreSQL resource first, then deploy `backend/` as a Docker app.

Required backend environment variables:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
CORS_ORIGINS=https://maison.cardvaults.app,http://localhost:5173
```

Backend health check:

```text
/health
```

Recommended public API domain:

```text
maison-api.cardvaults.app
```

## Frontend on Cloudflare Pages

Build settings:

```text
Root directory: frontend
Build command: npm run build
Output directory: dist
```

Required frontend environment variable:

```env
VITE_API_URL=https://maison-api.cardvaults.app
```

Recommended public frontend domain:

```text
maison.cardvaults.app
```

## Temporary Docker frontend

For a temporary non-Cloudflare deployment, build `frontend/Dockerfile.prod` with:

```bash
docker build \
  -f frontend/Dockerfile.prod \
  --build-arg VITE_API_URL=https://maison-api.178.105.44.71.sslip.io \
  -t maison-scout-frontend:latest \
  frontend
```

## Database migrations (Alembic)

The backend now manages its schema with Alembic instead of relying on
`Base.metadata.create_all()` plus ad-hoc `ALTER TABLE` calls at startup.
Migrations live in `backend/alembic/versions/`. The container's `CMD` runs
`alembic upgrade head` automatically before starting uvicorn (see
`backend/Dockerfile`), so on every normal deploy migrations are applied for
you.

The one case that needs a **manual, one-time** step is adopting Alembic on
an environment whose database already has the full schema created the old
way (this is the case for the current production database).

### Prerequisite for all commands below

Run them from `backend/`, with `DATABASE_URL` set to the target database
(the same value used by the app, e.g. exported from the environment or
passed inline):

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
```

`alembic.ini` intentionally does not hardcode a database URL — `alembic/env.py`
reads it from `app.config.settings.database_url` at runtime, exactly like the
app does.

### Case 1 — Existing database (current production)

The production Postgres database already has all tables (`listings`,
`users`, `search_profiles`, `user_listing_states`, `listing_sources`,
`listing_photos`, `price_history`, `crawl_runs`), created previously via
`create_all` + `ensure_schema()`. Do **not** run `alembic upgrade head`
first — it would try to `CREATE TABLE` on tables that already exist and
fail.

Instead, mark the database as already being at the initial revision,
without executing any DDL:

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
alembic stamp head
```

This only writes a row to the `alembic_version` table; it does not touch
any application table and does not read or write any application data.
After this one-time step, future deploys can safely run
`alembic upgrade head` (which the Docker image now does automatically) for
any migration added after `0001_initial_schema`.

**Do this exactly once**, before or during the first deploy of the
Alembic-enabled backend image to that environment.

### Case 2 — New database (fresh dev environment or new deployment)

A brand new, empty database has no tables and no `alembic_version` row, so
the normal command applies cleanly:

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
alembic upgrade head
```

This creates every table from scratch (equivalent to what `create_all` used
to do, but versioned and repeatable). This is also what the Docker image's
`CMD` runs automatically on every container start, which is a no-op if
already up to date.

### Creating a future migration

Whenever `backend/app/models.py` changes, generate a new migration and
review it before committing:

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
alembic revision --autogenerate -m "short description of the change"
# review the generated file in backend/alembic/versions/, then:
alembic upgrade head
```

Autogenerate diffs against whatever database `DATABASE_URL` points to, so
run it against a database that is already up to date with the previous
migration (a local/dev database is fine).

### Safety notes

- **Never drop or recreate the `maison-scout-postgres` volume/database** to
  "fix" a migration problem. If `alembic upgrade head` fails partway,
  investigate and fix the migration (or the database state) instead of
  resetting storage — that volume holds real production data.
- `alembic stamp head` never runs DDL and never deletes data; it only
  updates Alembic's own bookkeeping table (`alembic_version`). It is safe to
  run against the existing production database.
- Take a database backup/snapshot before the first `alembic stamp head` (or
  any migration) on production, as a general precaution, even though the
  stamp step itself is inert.

## Crawlers

Run every configured source:

```text
POST /api/crawl/all
```

Available source-specific endpoints:

```text
POST /api/crawl/green-acres
POST /api/crawl/bien-ici
```

The crawler reads active user search cities from the database. It keeps only house/villa-style listings and skips apartments, land, parking, and commercial listings where the source payload makes that detectable.
