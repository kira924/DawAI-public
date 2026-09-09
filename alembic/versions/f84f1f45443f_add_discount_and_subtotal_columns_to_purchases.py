"""Add discount and subtotal columns to purchases.

Revision ID: f84f1f45443f
Revises: 5e4fb43eb32c
Create Date: 2026-06-05 22:52:48.436097

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f84f1f45443f"
down_revision: str | Sequence[str] | None = "5e4fb43eb32c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_column_if_missing(table_name: str, column: sa.Column[object]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {item["name"] for item in inspector.get_columns(table_name)}
    if column.name not in existing_columns:
        op.add_column(table_name, column)


def upgrade() -> None:
    """Restore the historically applied purchase-calculation columns safely."""
    _add_column_if_missing(
        "purchase_items",
        sa.Column("base_cost", sa.Float(), server_default="0.0", nullable=False),
    )
    _add_column_if_missing(
        "purchase_items",
        sa.Column("discount_percentage", sa.Float(), server_default="0.0", nullable=False),
    )
    _add_column_if_missing(
        "purchase_items",
        sa.Column("discount_value", sa.Float(), server_default="0.0", nullable=False),
    )
    _add_column_if_missing(
        "purchase_orders",
        sa.Column("subtotal", sa.Float(), server_default="0.0", nullable=False),
    )
    _add_column_if_missing(
        "purchase_orders",
        sa.Column("total_discount", sa.Float(), server_default="0.0", nullable=False),
    )


def downgrade() -> None:
    """Remove the recovered purchase-calculation columns."""
    op.drop_column("purchase_orders", "total_discount")
    op.drop_column("purchase_orders", "subtotal")
    op.drop_column("purchase_items", "discount_value")
    op.drop_column("purchase_items", "discount_percentage")
    op.drop_column("purchase_items", "base_cost")
