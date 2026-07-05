# Maison Scout — Contexte projet (handoff)

Document de contexte à jour au **2026-07-05**. À donner à une IA / un nouvel intervenant pour reprendre le projet.

## 1. But du produit

Application web qui **centralise des annonces immobilières** (maisons/villas) scrapées automatiquement depuis plusieurs portails, d'abord pour **Fréjus / Saint-Raphaël**, partageable avec des amis qui suivent leurs propres villes. Pas d'import manuel : l'app scanne les sources configurées. Cible = **assistant de recherche personnel** : chaque utilisateur décrit ce qu'il cherche en langage naturel, et les annonces sont scorées + expliquées pour lui.

## 2. Stack & infra

- **Backend** : FastAPI (Python), SQLAlchemy 2.0, Alembic (migrations), psycopg (Postgres), httpx + BeautifulSoup (crawlers). Auth maison : mot de passe PBKDF2-SHA256, token type JWT signé HMAC (TTL 30 j), bearer.
- **Frontend** : React 19 + Vite, `lucide-react` (icônes), `leaflet` (carte). Un seul composant principal : `frontend/src/main.jsx` (+ `styles.css`, design system à base de CSS variables, police Manrope).
- **DB** : PostgreSQL 16 (conteneur `maison-scout-db`, base `maison_scout`, user `maison`, volume `maison-scout-postgres`).
- **Déploiement** : Docker sur **Coolify**. Déploiement **depuis `main`**. Le `CMD` du conteneur backend fait `alembic upgrade head` au démarrage.
- **Cron** : toutes les 6h → `POST /api/crawl/all` avec header `X-Crawl-Secret`.
- **Repo** : github.com/fyrex-FR/maison-scout
- **URLs prod (temporaires sslip.io)** : front `http://maison.178.105.44.71.sslip.io`, API `http://maison-api.178.105.44.71.sslip.io`, health `/health`.

### Variables d'environnement backend
`DATABASE_URL`, `CORS_ORIGINS`, `SECRET_KEY`, `ALLOW_OPEN_REGISTRATION`, `INVITE_CODES` (codes .env séparés par virgule), `CRAWL_SECRET` (secret machine-à-machine), `ADMIN_EMAILS` (emails admin séparés par virgule — **à définir en prod pour avoir un admin**). Frontend : `VITE_API_URL`.

## 3. Séparation d'architecture (important)

Trois responsabilités distinctes :
1. **Crawlers déterministes** (dans le backend) : collectent les données brutes/normalisées. Ne font jamais d'IA.
2. **Backend FastAPI** : source de vérité (stockage, validation, endpoints). **Ne stocke aucune clé OpenAI, n'appelle jamais l'IA.**
3. **OpenClaw / Jarvis** (worker externe, hors repo, avec intégration ChatGPT + navigateur) : fait le travail IA (analyse, parsing de prompts, scoring) ET le scraping des sources protégées, puis écrit dans le backend via des endpoints internes protégés par `X-Crawl-Secret`.

## 4. Modèle de données (tables)

- `listings` — annonce dédupliquée (title, city, postal_code, price_eur, living_area_m2, land_area_m2, rooms, bedrooms, energy_rating, description, score, status, created_at, updated_at, **latitude, longitude**). Une annonce a plusieurs `listing_sources`, plusieurs `listing_photos`, un `price_history`.
- `listing_sources` — (source, source_id unique, url) → permet qu'une même annonce vienne de plusieurs portails (dédup inter-sources).
- `listing_photos`, `price_history` (historique de prix par annonce).
- `users` — email, display_name, password_hash, created_at, **listings_seen_at** (watermark "vu"), **is_admin**.
- `search_profiles` — critères **classiques par ville** et par utilisateur (city, max_price_eur, min_living_area_m2, min_land_area_m2, min_bedrooms, enabled).
- `natural_search_profiles` — recherche **en langage naturel** par utilisateur (raw_prompt, criteria_json, weights_json, is_active, parsed_model, parsed_at) ; parsée par OpenClaw.
- `listing_match_scores` — score de correspondance **par (annonce, profil naturel)** (score, matched_reasons_json, missing_or_uncertain_json, dealbreakers_json, source_analysis_id).
- `listing_ai_analysis` — analyse IA **globale par annonce** (summary, features_json, red_flags_json, confidence_json, photo_observations_json, source_hash pour le cache).
- `user_listing_states` — statut (new/favorite/call/rejected) + note privée, **par utilisateur** (jamais partagés entre utilisateurs).
- `comparison_items` — comparatif par utilisateur (max 4).
- `semantic_dedup_decisions` — trace d'audit des fusions/rejets IA (ids en entiers bruts, pas de FK, pour rester lisibles après suppression du doublon).
- `invite_codes` — codes d'invitation gérés en base (code unique, active, note, used_count).
- `crawl_runs` — historique des scans (source, status, found_count, error, timestamps).

### Migrations Alembic (toutes additives, vérifiées sans drift)
`0001` schéma initial · `0002` comparison_items · `0003` semantic_dedup_decisions · `0004` ai_assistant_foundation (analyse + profils naturels + match scores) · `0005` listings_seen_watermark · `0006` admin_invites_geo (is_admin, lat/lng, invite_codes).
Note : sur la base prod pré-existante, Alembic a été adopté via `alembic stamp head` une fois (voir `docs/deployment.md`). Depuis, chaque migration additive s'applique via `alembic upgrade head` au déploiement.

## 5. API — modèle d'authentification

- **Endpoints utilisateur** : token bearer (`get_current_user`).
- **Endpoints admin** : bearer + `require_admin` (admin si `user.is_admin` OU email ∈ `ADMIN_EMAILS`).
- **Déclencheurs de crawl** : `require_crawl_access` = `X-Crawl-Secret` **ou** bearer valide.
- **Endpoints internes/worker/destructifs** : `require_crawl_secret` = `X-Crawl-Secret` **uniquement** (pas de token user) → semantic-dedup, ai/*, ingest.

### Endpoints
**Auth** : `POST /api/auth/register` (invite requis si des codes existent, .env ou DB ; crée par défaut les villes Frejus + Saint-Raphael), `POST /api/auth/login`, `GET /api/me`.
**Annonces** : `GET /api/listings` (filtrées aux villes suivies + critères classiques — une **donnée manquante n'exclut plus** l'annonce ; enrichies : statut/note par user, score + score_breakdown, ai_summary/red_flags/match_*, auto_flags, price_dropped/price_change_abs, is_new, lat/lng), `PATCH /api/listings/{id}/status` (partiel via model_fields_set), `GET /api/listings/{id}/price-history`, `POST /api/listings/mark-seen`.
**Profils classiques** : `GET/POST/PATCH/DELETE /api/search-profiles`.
**Profils langage naturel** : `GET/POST/PATCH/DELETE /api/natural-search-profiles`.
**Comparatif** : `GET /api/comparison`, `POST /api/comparison/{id}`, `DELETE /api/comparison/{id}` (max 4).
**Crawl** : `POST /api/crawl/{demo,green-acres,bien-ici,all}`, `POST /api/crawl/pap` (**opt-in, PAS dans /all ni le cron**), `GET /api/crawl-runs`.
**Dédup sémantique** (secret only) : `GET /api/semantic-dedup/candidates`, `POST /api/semantic-dedup/merge`, `POST /api/semantic-dedup/reject`.
**Worker IA** (secret only) : `GET /api/ai/listings/pending-analysis`, `PUT /api/ai/listings/{id}/analysis`, `GET /api/ai/natural-search-profiles/pending-parse`, `PUT /api/ai/natural-search-profiles/{id}/parse`, `GET /api/ai/pending-match-scores` (enrichi de source_analysis + natural_search_profile), `PUT /api/ai/match-scores`.
**Admin** (admin only) : `GET /api/admin/users`, `GET/POST/PATCH /api/admin/invite-codes`.
**Ingestion externe** (secret only) : `POST /api/ingest/listings` (batch ≤ 500, une source/requête ; réutilise le pipeline dédup/scoring/photos/prix).
**Santé** : `GET /health` (teste la DB, 503 si down).

## 6. Sources d'annonces

- **Green-Acres** (`crawlers/green_acres.py`) : opérationnel (HTML).
- **Bien'ici** (`crawlers/bien_ici.py`) : opérationnel (JSON place.json + realEstateAds.json). Extrait aussi lat/lng (emplacement `blurInfo.position` — **hypothèse à confirmer** contre le vrai format API ; extraction tolérante).
- **PAP** (`crawlers/pap.py`) : crawler httpx présent mais **inutilisable** (pap.fr bloqué Cloudflare 403). La vraie collecte PAP passe par OpenClaw (navigateur) → `/api/ingest/listings`.
- **SeLoger** : bloqué DataDome, jamais intégré en httpx. Idem PAP → via OpenClaw.
- **Déduplication** : à l'ingestion (`ingest.upsert_listing`), dédup par (source, source_id) + heuristique inter-sources conservative (même ville canonique + prix ±2% + surface ±2m²). Une passe **sémantique IA** (via OpenClaw + endpoints semantic-dedup) rattrape les doublons hors tolérance (fusion prudente, sans perte de données par utilisateur, avec trace d'audit). Villes normalisées via `app/cities.py::canonical_city_name` (source unique de vérité).

## 7. OpenClaw / Jarvis (worker externe, hors repo)

- **Worker assistant IA** : EN PLACE. Script `scripts/maison_scout_assistant_worker.py` côté OpenClaw, cron 6h à H+35 UTC (après crawl+dédup). Fait Job A (analyse annonce → `listing_ai_analysis`), Job B (parse prompt naturel), Job C (scoring → `listing_match_scores`). Analyse actuelle = **heuristique prudente**, pas encore de vision photo fine (piscine/escaliers/accès) — évolution prévue. Alerte Telegram en cas d'échec seulement.
- **Scraper PAP/SeLoger** : À CODER côté OpenClaw (brief remis). Doit scraper avec navigateur (passe Cloudflare/DataDome), filtrer maisons/villas, et POSTer vers `/api/ingest/listings`.
- **Backups Postgres** : À METTRE EN PLACE côté OpenClaw/Coolify (brief remis) — backups Coolify natifs + destination off-site S3-compatible + test de restauration + alerte échec.
- Docs de référence : `docs/assistant-architecture.md`, `docs/openclaw-assistant-worker.md`, `docs/semantic-dedup.md`, `docs/external-ingestion.md`.

## 8. Fonctionnalités livrées

Auth + invitations · profils par ville (critères classiques) · profils en langage naturel (UI) · liste + fiche détail (galerie photos) · statuts + notes privées par utilisateur · comparatif 2-4 annonces (tableau prix/prix-m²/surface/…) · scoring **explicable** (score_breakdown) · **signaux automatiques déterministes** (DPE F/G, sans photo, surface/prix manquants, prix/m² anormal vs médiane ville) · **baisse de prix** (badge + historique + sparkline SVG) · **prix/m²** partout · indicateur **Nouveau / déjà-vu** (watermark) · blocs IA (résumé, red flags, correspondance avec la recherche) · tri "Pertinence IA" · **page admin** (utilisateurs + gestion codes d'invitation) · **vue carte** Leaflet/OSM (marqueurs colorés par score) · endpoint d'**ingestion externe**.

## 9. Conventions de dev (utilisées jusqu'ici)

- Migrations **strictement additives**, vérifiées **sans drift** (upgrade head puis `alembic revision --autogenerate` → doit être vide) et réversibles. Jamais toucher/recréer le volume `maison-scout-postgres`.
- **Aucun secret en dur** ; `X-Crawl-Secret` jamais loggué ; aucune clé OpenAI dans le repo.
- Les données par utilisateur (statuts, notes, comparatif) ne doivent jamais être écrasées/mélangées entre utilisateurs (ex. la fusion sémantique préserve chaque état utilisateur).
- Vérification systématique avant push : `pytest` complet (**~123 tests** actuellement, tous verts), contrôle no-drift Alembic, `npm run build`, et **preview réelle** (backend+frontend lancés, données seedées, parcours vérifié au navigateur).
- Historique de commits par lots logiques, déploiement par push sur `main`.

## 10. Reste à faire

- **Backups Postgres** (risque n°1 : rien ne sauvegarde la base aujourd'hui) — brief remis à OpenClaw.
- **Scraper PAP/SeLoger** côté OpenClaw — brief remis.
- **Notifications** (résumé quotidien / gros score / baisse de prix ; canal Telegram ou email ; stocker un last_notified_at pour ne pas spammer).
- **Crawl en tâche de fond** (aujourd'hui `POST /api/crawl/all` est synchrone → risque de timeout quand le nombre de villes/sources augmente ; touche le cron + le bouton Scanner, à faire prudemment).
- **Confirmer le vrai format des coordonnées Bien'ici** (l'extraction lat/lng est une hypothèse).
- **Passe vision fine** côté OpenClaw (analyse photo poussée).
- **Prod propre** : domaine final (ex. maison.cardvaults.app), front sur Cloudflare Pages, API sur domaine propre, monitoring health + cron.

## 11. Pièges / points d'attention

- Sur la table `users` déjà peuplée en prod, toute nouvelle colonne NOT NULL doit avoir un `server_default` (fait pour `is_admin`).
- Pour être admin en prod : définir `ADMIN_EMAILS` sur le conteneur backend, sinon aucun admin et le bouton n'apparaît pas.
- La carte se remplit progressivement (seules les annonces re-crawlées avec coords apparaissent) ; dépend du format coords Bien'ici (à confirmer).
- `crawlers/pap.py` (httpx) est mort (Cloudflare) — ne pas le brancher au cron ; la collecte PAP réelle passe par l'ingestion externe.
