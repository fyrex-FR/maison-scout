"""add semantic dedup decisions table

Revision ID: 0003_semantic_dedup_decisions
Revises: 0002_comparison_items
Create Date: 2026-07-04

Tracks external AI dedup decisions so the agent can avoid reviewing the same
listing pair repeatedly and we keep a lightweight merge/reject audit trail.
Listing ids are intentionally stored as plain integers instead of foreign keys:
merged duplicate Listing rows are deleted after their sources/states/history are
moved to the surviving row, but the decision record should remain readable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_semantic_dedup_decisions"
down_revision: Union[str, None] = "0002_comparison_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "semantic_dedup_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("left_listing_id", sa.Integer(), nullable=False),
        sa.Column("right_listing_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("left_listing_id", "right_listing_id", name="uq_semantic_dedup_pair"),
    )
    op.create_index("ix_semantic_dedup_decisions_left_listing_id", "semantic_dedup_decisions", ["left_listing_id"])
    op.create_index("ix_semantic_dedup_decisions_right_listing_id", "semantic_dedup_decisions", ["right_listing_id"])
    op.create_index("ix_semantic_dedup_decisions_status", "semantic_dedup_decisions", ["status"])


def downgrade() -> None:
    op.drop_table("semantic_dedup_decisions")
