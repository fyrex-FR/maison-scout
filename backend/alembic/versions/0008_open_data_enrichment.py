"""add open-data enrichment: city_market_stats (DVF) + listings.georisques_json

Revision ID: 0008_open_data_enrichment
Revises: 0007_off_market
Create Date: 2026-07-05

Adds the storage needed for deterministic open-data enrichment (no AI, no
API key required):
- A new `city_market_stats` table caching, per canonical city, the median
  real sale price/m2 for houses computed from DVF (Etalab) data. Cached
  because DVF CSVs are fetched over HTTP per-commune and refreshed at most
  every ~30 days (see app.enrichment.dvf.refresh_city_stats).
- Two nullable columns on `listings` (`georisques_json`, `georisques_checked_at`)
  caching the compact Georisques natural/technological risk summary for a
  listing's coordinates, refreshed on the same cadence
  (see app.enrichment.georisques.enrich_listings_risks).

Strictly additive: no existing column/table is altered or dropped, no
existing data is touched. Safe to run against a populated production
database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008_open_data_enrichment"
down_revision: Union[str, None] = "0007_off_market"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "city_market_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("insee_code", sa.String(length=8), nullable=True),
        sa.Column("median_price_per_m2_house", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("period_label", sa.String(length=64), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_city_market_stats_city",
        "city_market_stats",
        ["city"],
        unique=True,
    )
    op.add_column("listings", sa.Column("georisques_json", sa.JSON(), nullable=True))
    op.add_column("listings", sa.Column("georisques_checked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("listings", "georisques_checked_at")
    op.drop_column("listings", "georisques_json")
    op.drop_index("ix_city_market_stats_city", table_name="city_market_stats")
    op.drop_table("city_market_stats")
