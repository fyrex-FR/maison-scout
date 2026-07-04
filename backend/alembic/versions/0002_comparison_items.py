"""add comparison_items table

Revision ID: 0002_comparison_items
Revises: 0001_initial_schema
Create Date: 2026-07-04

Adds the table backing the "comparatif" feature: up to a few listings a
user pins side by side. Pure addition, no data migration needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_comparison_items"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comparison_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "listing_id", name="uq_comparison_user_listing"),
    )
    op.create_index("ix_comparison_items_user_id", "comparison_items", ["user_id"])
    op.create_index("ix_comparison_items_listing_id", "comparison_items", ["listing_id"])


def downgrade() -> None:
    op.drop_table("comparison_items")
