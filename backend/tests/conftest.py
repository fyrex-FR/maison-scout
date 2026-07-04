"""Test-session bootstrap.

`app.db` builds a module-level SQLAlchemy engine from `settings.database_url`
as soon as it is imported, which normally points at Postgres (see
app/config.py) and requires the `psycopg` driver to be installed. That's fine
in the real app/Docker image, but the data-pipeline unit tests in this
directory don't need a real database at all -- they use their own in-memory
SQLite engine (see test_ingest.py).

To keep `pytest` runnable out of the box (no Postgres, no psycopg required)
we default DATABASE_URL to SQLite *before* any `app.*` module is imported,
unless the environment already provides one. This file intentionally does
not touch app/db.py, which is outside this pipeline's edit scope.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
