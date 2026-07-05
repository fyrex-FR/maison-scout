"""add per-user listings-seen watermark

Revision ID: 0005_listings_seen_watermark
Revises: 0004_ai_assistant_foundation
Create Date: 2026-07-05

Adds a single nullable column to track, per user, the timestamp of the last
"mark all as seen" action. Used to compute an is_new flag on listings
(created_at > listings_seen_at) without touching any existing data. Purely
additive: no backfill, no existing column touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_listings_seen_watermark"
down_revision: Union[str, None] = "0004_ai_assistant_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("listings_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "listings_seen_at")
