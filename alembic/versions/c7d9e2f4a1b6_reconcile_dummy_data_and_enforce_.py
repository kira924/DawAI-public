"""Reconcile dummy data and enforce canonical values.

Revision ID: c7d9e2f4a1b6
Revises: b8f9c2d14a6e
Create Date: 2026-08-19 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d9e2f4a1b6"
down_revision: str | Sequence[str] | None = "b8f9c2d14a6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Normalize approved dummy data and prevent the same invalid states."""
    op.execute("UPDATE product_batches SET quantity = 0 WHERE quantity < 0")
    op.execute(
        """
        UPDATE products AS product
        SET total_parts = COALESCE(
            (
                SELECT SUM(batch.quantity)
                FROM product_batches AS batch
                WHERE batch.product_id = product.id
                  AND batch.tenant_id = product.tenant_id
            ),
            0
        )
        WHERE product.total_parts IS DISTINCT FROM COALESCE(
            (
                SELECT SUM(batch.quantity)
                FROM product_batches AS batch
                WHERE batch.product_id = product.id
                  AND batch.tenant_id = product.tenant_id
            ),
            0
        )
        """
    )
    op.execute(
        """
        UPDATE invoices
        SET payment_type = CASE
            WHEN LOWER(TRIM(COALESCE(payment_type, ''))) = 'credit' THEN 'credit'
            ELSE 'cash'
        END
        """
    )
    op.execute(
        """
        UPDATE users
        SET role = CASE LOWER(TRIM(COALESCE(role, '')))
            WHEN 'admin' THEN 'super_admin'
            WHEN 'super_admin' THEN 'super_admin'
            WHEN 'manager' THEN 'manager'
            WHEN 'pharmacist' THEN 'pharmacist'
            ELSE 'pharmacist'
        END
        """
    )

    op.alter_column(
        "invoices",
        "payment_type",
        existing_type=sa.String(),
        nullable=False,
        server_default="cash",
    )
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(),
        nullable=False,
        server_default="pharmacist",
    )
    op.create_check_constraint(
        "ck_invoices_payment_type_canonical",
        "invoices",
        "payment_type IN ('cash', 'credit')",
    )
    op.create_check_constraint(
        "ck_users_role_canonical",
        "users",
        "role IN ('super_admin', 'manager', 'pharmacist')",
    )
    op.create_check_constraint(
        "ck_products_total_parts_nonnegative",
        "products",
        "total_parts >= 0",
    )
    op.create_check_constraint(
        "ck_product_batches_quantity_nonnegative",
        "product_batches",
        "quantity >= 0",
    )


def downgrade() -> None:
    """Remove constraints while retaining the normalized dummy values."""
    op.drop_constraint(
        "ck_product_batches_quantity_nonnegative",
        "product_batches",
        type_="check",
    )
    op.drop_constraint(
        "ck_products_total_parts_nonnegative",
        "products",
        type_="check",
    )
    op.drop_constraint("ck_users_role_canonical", "users", type_="check")
    op.drop_constraint("ck_invoices_payment_type_canonical", "invoices", type_="check")
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(),
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "invoices",
        "payment_type",
        existing_type=sa.String(),
        nullable=True,
        server_default=None,
    )
