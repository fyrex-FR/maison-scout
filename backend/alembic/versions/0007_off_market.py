"""add listings.off_market_at for off-market lifecycle tracking

Revision ID: 0007_off_market
Revises: 0006_admin_invites_geo
Create Date: 2026-07-05

Adds a single nullable column used to track when a listing was last
determined to be off the market (no longer seen on any source within the
configured threshold): listings.off_market_at (nullable DateTime, default
NULL). NULL means the listing is still considered active. This is populated
by app.lifecycle.refresh_off_market_status, not by this migration.
Strictly additive: no existing column is altered or dropped, no existing
data is touched. Safe to run against a populated production database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007_off_market"
down_revision: Union[str, None] = "0006_admin_invites_geo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("off_market_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("listings", "off_market_at")
