# Maison Scout

Centralise les annonces immobilieres pour la recherche Fréjus / Saint-Raphael.

## Stack

- `backend/`: FastAPI, SQLAlchemy, PostgreSQL
- `frontend/`: React + Vite
- `docker-compose.yml`: stack locale backend + frontend + Postgres

## Demarrage local

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

- Frontend: http://localhost:5173
- API: http://localhost:8000
- Health: http://localhost:8000/health

## Premier perimetre

- Zone: Frejus, Saint-Raphael
- Ingestion automatisee, pas d'import manuel
- Premier crawler: Green-Acres
- Sources suivantes a brancher: Bien'ici, SeLoger
- DB sur Coolify/PostgreSQL pour la prod

## API utile

- `GET /api/listings`
- `POST /api/crawl/green-acres`
- `GET /api/crawl-runs`

Voir aussi `docs/deployment.md`.
