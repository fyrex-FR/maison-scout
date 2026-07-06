"""add crawl_jobs: backend-owned control plane for crawl execution

Revision ID: 0009_crawl_jobs
Revises: 0008_open_data_enrichment
Create Date: 2026-07-06

Today crawl execution is split across two brains: synchronous in-backend
endpoints (httpx sources) and blind OpenClaw crons (browser sources), with no
shared state between them. This migration adds the `crawl_jobs` table so the
backend becomes the single control plane: it enqueues one job per crawl
request, the in-process executor claims/executes "backend" jobs directly, and
the external OpenClaw executor pulls its own "openclaw" jobs via the
crawl-secret-protected job endpoints (see app/crawl_jobs.py) instead of
running on a blind timer.

Strictly additive: no existing table/column is altered or dropped. Safe to
run against a populated production database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0009_crawl_jobs"
down_revision: Union[str, None] = "0008_open_data_enrichment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("executor", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("found_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("crawl_run_id", sa.Integer(), sa.ForeignKey("crawl_runs.id"), nullable=True),
    )
    op.create_index("ix_crawl_jobs_source", "crawl_jobs", ["source"])
    op.create_index("ix_crawl_jobs_executor", "crawl_jobs", ["executor"])
    op.create_index("ix_crawl_jobs_status", "crawl_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_crawl_jobs_status", table_name="crawl_jobs")
    op.drop_index("ix_crawl_jobs_executor", table_name="crawl_jobs")
    op.drop_index("ix_crawl_jobs_source", table_name="crawl_jobs")
    op.drop_table("crawl_jobs")
