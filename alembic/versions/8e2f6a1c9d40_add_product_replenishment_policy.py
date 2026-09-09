"""add product replenishment policy

Revision ID: 8e2f6a1c9d40
Revises: c4d8e2f6a190
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8e2f6a1c9d40"
down_revision: str | Sequence[str] | None = "c4d8e2f6a190"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("reorder_point_parts", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("target_stock_parts", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_products_replenishment_policy_context",
        "products",
        "(reorder_point_parts IS NULL AND target_stock_parts IS NULL) OR "
        "(reorder_point_parts IS NOT NULL AND target_stock_parts IS NOT NULL "
        "AND reorder_point_parts >= 0 AND target_stock_parts > reorder_point_parts)",
    )
    op.create_index(
        "ix_products_tenant_replenishment_configured",
        "products",
        ["tenant_id", "reorder_point_parts", "id"],
        unique=False,
        postgresql_where=sa.text(
            "reorder_point_parts IS NOT NULL AND target_stock_parts IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_products_tenant_replenishment_configured", table_name="products")
    op.drop_constraint(
        "ck_products_replenishment_policy_context",
        "products",
        type_="check",
    )
    op.drop_column("products", "target_stock_parts")
    op.drop_column("products", "reorder_point_parts")
