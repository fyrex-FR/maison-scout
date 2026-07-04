FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic

# Apply database migrations before starting the API. `alembic upgrade head`
# is a no-op if the database is already at the latest revision.
#
# IMPORTANT (one-time step): on an environment that already has the schema
# from before Alembic was introduced (create_all + ensure_schema()), the
# very first deploy of this image must be preceded by a manual
# `alembic stamp head` run against that database, otherwise this
# `alembic upgrade head` will try to CREATE TABLEs that already exist and
# fail. See docs/deployment.md for the exact one-time command. Fresh
# databases don't need any manual step: `alembic upgrade head` creates
# everything from scratch.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

