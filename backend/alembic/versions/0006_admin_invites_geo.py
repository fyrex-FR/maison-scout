"""add admin flag, geo fields, and invite codes table

Revision ID: 0006_admin_invites_geo
Revises: 0005_listings_seen_watermark
Create Date: 2026-07-05

Adds the storage foundation for admin/role management, DB-backed invite
codes, and the future listings map view:
  - users.is_admin (NOT NULL, server_default false) so existing rows on a
    populated Postgres table get a safe default with no manual backfill.
  - listings.latitude / listings.longitude (nullable Float) to be populated
    later by the ingest pipeline (owned by another agent) -- declared here
    only, no backfill.
  - invite_codes table, a brand-new table with no interaction with existing
    data.
Strictly additive: no existing column is altered or dropped, no existing
data is touched. Safe to run against a populated production database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_admin_invites_geo"
down_revision: Union[str, None] = "0005_listings_seen_watermark"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("listings", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("longitude", sa.Float(), nullable=True))

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_invite_codes_code", "invite_codes", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_invite_codes_code", table_name="invite_codes")
    op.drop_table("invite_codes")
    op.drop_column("listings", "longitude")
    op.drop_column("listings", "latitude")
    op.drop_column("users", "is_admin")
