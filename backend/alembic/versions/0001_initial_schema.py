"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-04

This migration reflects the schema as it exists today in app/models.py,
which in production was originally created via Base.metadata.create_all()
plus the ad-hoc ensure_schema() ALTER TABLE calls in app/main.py.

IMPORTANT: on an existing database (already created via create_all), do NOT
run `alembic upgrade head` for this revision — it would try to CREATE TABLEs
that already exist and fail (or, worse, if it somehow succeeded partially,
risk clobbering data). Instead run `alembic stamp head` once to tell Alembic
"this database is already at this revision" without executing any DDL.
See docs/deployment.md for the full runbook.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("price_eur", sa.Integer(), nullable=True),
        sa.Column("living_area_m2", sa.Integer(), nullable=True),
        sa.Column("land_area_m2", sa.Integer(), nullable=True),
        sa.Column("rooms", sa.Integer(), nullable=True),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("energy_rating", sa.String(length=8), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_listings_city", "listings", ["city"])
    op.create_index("ix_listings_status", "listings", ["status"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("max_price_eur", sa.Integer(), nullable=True),
        sa.Column("min_living_area_m2", sa.Integer(), nullable=True),
        sa.Column("min_land_area_m2", sa.Integer(), nullable=True),
        sa.Column("min_bedrooms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_search_profiles_user_id", "search_profiles", ["user_id"])
    op.create_index("ix_search_profiles_city", "search_profiles", ["city"])
    op.create_index("ix_search_profiles_source", "search_profiles", ["source"])

    op.create_table(
        "user_listing_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "listing_id", name="uq_user_listing_state"),
    )
    op.create_index("ix_user_listing_states_user_id", "user_listing_states", ["user_id"])
    op.create_index("ix_user_listing_states_listing_id", "user_listing_states", ["listing_id"])
    op.create_index("ix_user_listing_states_status", "user_listing_states", ["status"])

    op.create_table(
        "listing_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_listing_source"),
    )
    op.create_index("ix_listing_sources_source", "listing_sources", ["source"])

    op.create_table(
        "listing_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("price_eur", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_price_history_listing_id", "price_history", ["listing_id"])

    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("found_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_crawl_runs_source", "crawl_runs", ["source"])
    op.create_index("ix_crawl_runs_status", "crawl_runs", ["status"])


def downgrade() -> None:
    op.drop_table("crawl_runs")
    op.drop_table("price_history")
    op.drop_table("listing_photos")
    op.drop_table("listing_sources")
    op.drop_table("user_listing_states")
    op.drop_table("search_profiles")
    op.drop_table("users")
    op.drop_table("listings")
