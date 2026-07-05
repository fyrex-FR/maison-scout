"""add assistant analysis and matching tables

Revision ID: 0004_ai_assistant_foundation
Revises: 0003_semantic_dedup_decisions
Create Date: 2026-07-05

Adds the storage foundation for the Maison Scout assistant architecture:
global per-listing AI analysis, per-user natural-language search profiles, and
per-profile listing match scores. Model calls still run outside the backend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_ai_assistant_foundation"
down_revision: Union[str, None] = "0003_semantic_dedup_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listing_ai_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("red_flags_json", sa.JSON(), nullable=False),
        sa.Column("confidence_json", sa.JSON(), nullable=False),
        sa.Column("photo_observations_json", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("listing_id", name="uq_listing_ai_analysis_listing"),
    )
    op.create_index("ix_listing_ai_analysis_listing_id", "listing_ai_analysis", ["listing_id"])
    op.create_index("ix_listing_ai_analysis_source_hash", "listing_ai_analysis", ["source_hash"])

    op.create_table(
        "natural_search_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("raw_prompt", sa.Text(), nullable=False),
        sa.Column("criteria_json", sa.JSON(), nullable=False),
        sa.Column("weights_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("parsed_model", sa.String(length=120), nullable=True),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_natural_search_profiles_user_id", "natural_search_profiles", ["user_id"])
    op.create_index("ix_natural_search_profiles_is_active", "natural_search_profiles", ["is_active"])

    op.create_table(
        "listing_match_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column(
            "natural_search_profile_id",
            sa.Integer(),
            sa.ForeignKey("natural_search_profiles.id"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("matched_reasons_json", sa.JSON(), nullable=False),
        sa.Column("missing_or_uncertain_json", sa.JSON(), nullable=False),
        sa.Column("dealbreakers_json", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("source_analysis_id", sa.Integer(), sa.ForeignKey("listing_ai_analysis.id"), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("listing_id", "natural_search_profile_id", name="uq_listing_match_profile"),
    )
    op.create_index("ix_listing_match_scores_listing_id", "listing_match_scores", ["listing_id"])
    op.create_index(
        "ix_listing_match_scores_natural_search_profile_id",
        "listing_match_scores",
        ["natural_search_profile_id"],
    )
    op.create_index("ix_listing_match_scores_score", "listing_match_scores", ["score"])
    op.create_index("ix_listing_match_scores_source_analysis_id", "listing_match_scores", ["source_analysis_id"])


def downgrade() -> None:
    op.drop_table("listing_match_scores")
    op.drop_table("natural_search_profiles")
    op.drop_table("listing_ai_analysis")

