"""repair crawl_jobs table from early pull-job prototype

Revision ID: 0010_crawl_jobs_compat
Revises: 0009_crawl_jobs
Create Date: 2026-07-06

The first production deployment of the crawl job queue briefly created a
prototype-shaped `crawl_jobs` table under revision 0009. The final 0009
migration in GitHub has a smaller table. Because Alembic already considered
0009 applied in prod, this migration bridges both shapes safely:

* add final columns when they are missing;
* relax prototype-only NOT NULL columns so final-model inserts work;
* backfill executor on prototype rows that were queued before the final code.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0010_crawl_jobs_compat"
down_revision: Union[str, None] = "0009_crawl_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> dict[str, dict]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"]: column for column in inspector.get_columns("crawl_jobs")}


def upgrade() -> None:
    columns = _columns()

    if "requested_by" not in columns:
        op.add_column("crawl_jobs", sa.Column("requested_by", sa.String(length=120), nullable=True))
    if "crawl_run_id" not in columns:
        op.add_column("crawl_jobs", sa.Column("crawl_run_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_crawl_jobs_crawl_run_id_crawl_runs",
            "crawl_jobs",
            "crawl_runs",
            ["crawl_run_id"],
            ["id"],
        )

    # Prototype-only columns can remain for compatibility, but they must not
    # block inserts from the final SQLAlchemy model, which no longer writes
    # city/target_json/attempts/updated_at.
    nullable_repairs = {
        "city": sa.String(length=120),
        "target_json": sa.JSON(),
        "attempts": sa.Integer(),
        "updated_at": sa.DateTime(),
    }
    columns = _columns()
    for name, column_type in nullable_repairs.items():
        if name in columns and not columns[name]["nullable"]:
            op.alter_column("crawl_jobs", name, existing_type=column_type, nullable=True)

    op.execute(
        """
        UPDATE crawl_jobs
        SET executor = CASE
            WHEN source IN ('green-acres', 'bien-ici', 'paruvendu') THEN 'backend'
            ELSE 'openclaw'
        END
        WHERE executor IS NULL
        """
    )

    columns = _columns()
    if "executor" in columns and columns["executor"]["nullable"]:
        op.alter_column("crawl_jobs", "executor", existing_type=sa.String(length=32), nullable=False)


def downgrade() -> None:
    columns = _columns()
    if "crawl_run_id" in columns:
        op.drop_constraint("fk_crawl_jobs_crawl_run_id_crawl_runs", "crawl_jobs", type_="foreignkey")
        op.drop_column("crawl_jobs", "crawl_run_id")
    if "requested_by" in columns:
        op.drop_column("crawl_jobs", "requested_by")
