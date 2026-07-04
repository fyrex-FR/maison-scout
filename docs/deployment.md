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

## First crawler

The first production crawler is Green-Acres:

```text
POST /api/crawl/green-acres
```

It currently scans:

- Frejus
- Saint-Raphael

It keeps only house/villa-style listings and skips apartments, land, parking, and commercial listings.
