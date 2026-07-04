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
- Sources a brancher en priorite: Bien'ici, Green-Acres, SeLoger
- DB sur Coolify/PostgreSQL pour la prod

