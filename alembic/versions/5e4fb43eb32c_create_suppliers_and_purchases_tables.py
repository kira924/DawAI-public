"""Create suppliers and purchases tables.

Revision ID: 5e4fb43eb32c
Revises: 428427b94993
Create Date: 2026-06-05 22:31:25.351926

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e4fb43eb32c"
down_revision: str | Sequence[str] | None = "428427b94993"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _require_columns(table_name: str, required_columns: set[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
    missing_columns = required_columns - actual_columns
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(f"Existing {table_name} table is missing columns: {missing}")


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(op.f(index_name), table_name, columns, unique=False)


def upgrade() -> None:
    """Restore the historically applied purchase-table revision safely."""
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("suppliers"):
        op.create_table(
            "suppliers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("contact_info", sa.String(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        _require_columns("suppliers", {"id", "name", "contact_info", "tenant_id"})

    _create_index_if_missing("suppliers", "ix_suppliers_id", ["id"])
    _create_index_if_missing("suppliers", "ix_suppliers_name", ["name"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("purchase_orders"):
        op.create_table(
            "purchase_orders",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("total_cost", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        _require_columns(
            "purchase_orders",
            {"id", "total_cost", "created_at", "supplier_id", "user_id", "tenant_id"},
        )

    _create_index_if_missing("purchase_orders", "ix_purchase_orders_id", ["id"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("purchase_items"):
        op.create_table(
            "purchase_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("purchase_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("unit_cost", sa.Float(), nullable=False),
            sa.Column("subtotal", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["purchase_id"], ["purchase_orders.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        _require_columns(
            "purchase_items",
            {"id", "purchase_id", "product_id", "quantity", "unit_cost", "subtotal"},
        )

    _create_index_if_missing("purchase_items", "ix_purchase_items_id", ["id"])


def downgrade() -> None:
    """Remove the recovered purchase tables in dependency order."""
    op.drop_index(op.f("ix_purchase_items_id"), table_name="purchase_items")
    op.drop_table("purchase_items")
    op.drop_index(op.f("ix_purchase_orders_id"), table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index(op.f("ix_suppliers_name"), table_name="suppliers")
    op.drop_index(op.f("ix_suppliers_id"), table_name="suppliers")
    op.drop_table("suppliers")
