"""Enforce same-tenant relationships throughout purchases.

Revision ID: 8c4f1d2e6a90
Revises: 3f2a9c7d1e84
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8c4f1d2e6a90"
down_revision: str | Sequence[str] | None = "3f2a9c7d1e84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_existing_relationships_are_tenant_safe() -> None:
    checks = {
        "purchase order supplier": """
            SELECT COUNT(*)
            FROM purchase_orders AS purchase_order
            JOIN suppliers AS supplier ON supplier.id = purchase_order.supplier_id
            WHERE supplier.tenant_id IS DISTINCT FROM purchase_order.tenant_id
        """,
        "purchase order user": """
            SELECT COUNT(*)
            FROM purchase_orders AS purchase_order
            JOIN users AS app_user ON app_user.id = purchase_order.user_id
            WHERE app_user.tenant_id IS DISTINCT FROM purchase_order.tenant_id
        """,
        "purchase item product": """
            SELECT COUNT(*)
            FROM purchase_items AS purchase_item
            JOIN purchase_orders AS purchase_order
              ON purchase_order.id = purchase_item.purchase_id
            JOIN products AS product ON product.id = purchase_item.product_id
            WHERE product.tenant_id IS DISTINCT FROM purchase_order.tenant_id
        """,
        "supplier payment supplier": """
            SELECT COUNT(*)
            FROM supplier_payments AS supplier_payment
            JOIN suppliers AS supplier ON supplier.id = supplier_payment.supplier_id
            WHERE supplier.tenant_id IS DISTINCT FROM supplier_payment.tenant_id
        """,
        "supplier payment user": """
            SELECT COUNT(*)
            FROM supplier_payments AS supplier_payment
            JOIN users AS app_user ON app_user.id = supplier_payment.user_id
            WHERE app_user.tenant_id IS DISTINCT FROM supplier_payment.tenant_id
        """,
        "purchase return supplier": """
            SELECT COUNT(*)
            FROM purchase_returns AS purchase_return
            JOIN suppliers AS supplier ON supplier.id = purchase_return.supplier_id
            WHERE supplier.tenant_id IS DISTINCT FROM purchase_return.tenant_id
        """,
        "purchase return user": """
            SELECT COUNT(*)
            FROM purchase_returns AS purchase_return
            JOIN users AS app_user ON app_user.id = purchase_return.user_id
            WHERE app_user.tenant_id IS DISTINCT FROM purchase_return.tenant_id
        """,
        "purchase return order or supplier": """
            SELECT COUNT(*)
            FROM purchase_returns AS purchase_return
            JOIN purchase_orders AS purchase_order
              ON purchase_order.id = purchase_return.purchase_order_id
            WHERE purchase_return.purchase_order_id IS NOT NULL
              AND (
                purchase_order.tenant_id IS DISTINCT FROM purchase_return.tenant_id
                OR purchase_order.supplier_id IS DISTINCT FROM purchase_return.supplier_id
              )
        """,
        "purchase return item product": """
            SELECT COUNT(*)
            FROM purchase_return_items AS return_item
            JOIN purchase_returns AS purchase_return
              ON purchase_return.id = return_item.return_id
            JOIN products AS product ON product.id = return_item.product_id
            WHERE product.tenant_id IS DISTINCT FROM purchase_return.tenant_id
        """,
    }
    connection = op.get_bind()
    failures = {
        relationship: int(connection.scalar(sa.text(query)) or 0)
        for relationship, query in checks.items()
    }
    unsafe = {relationship: count for relationship, count in failures.items() if count}
    if unsafe:
        summary = ", ".join(
            f"{relationship}={count}" for relationship, count in sorted(unsafe.items())
        )
        raise RuntimeError(f"Unsafe purchase tenant relationships detected: {summary}")


def _drop_legacy_foreign_key(table_name: str, constrained_columns: Sequence[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    matching = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key["constrained_columns"] == list(constrained_columns)
    ]
    if len(matching) != 1 or not matching[0]["name"]:
        columns = ", ".join(constrained_columns)
        raise RuntimeError(
            f"Expected one named foreign key on {table_name}({columns}), found {len(matching)}"
        )
    op.drop_constraint(matching[0]["name"], table_name, type_="foreignkey")


def upgrade() -> None:
    """Fail closed on unsafe rows and add same-tenant purchase constraints."""
    _assert_existing_relationships_are_tenant_safe()

    op.create_unique_constraint(
        "uq_suppliers_id_tenant_id",
        "suppliers",
        ["id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_products_id_tenant_id",
        "products",
        ["id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_purchase_orders_id_tenant_id",
        "purchase_orders",
        ["id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_purchase_orders_id_supplier_tenant_id",
        "purchase_orders",
        ["id", "supplier_id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_purchase_returns_id_tenant_id",
        "purchase_returns",
        ["id", "tenant_id"],
    )

    op.add_column("purchase_items", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE purchase_items AS purchase_item
        SET tenant_id = purchase_order.tenant_id
        FROM purchase_orders AS purchase_order
        WHERE purchase_order.id = purchase_item.purchase_id
        """
    )
    op.alter_column("purchase_items", "tenant_id", existing_type=sa.Integer(), nullable=False)

    op.add_column(
        "purchase_return_items",
        sa.Column("tenant_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE purchase_return_items AS return_item
        SET tenant_id = purchase_return.tenant_id
        FROM purchase_returns AS purchase_return
        WHERE purchase_return.id = return_item.return_id
        """
    )
    op.alter_column(
        "purchase_return_items",
        "tenant_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    _drop_legacy_foreign_key("purchase_orders", ["supplier_id"])
    _drop_legacy_foreign_key("purchase_orders", ["user_id"])
    _drop_legacy_foreign_key("purchase_items", ["purchase_id"])
    _drop_legacy_foreign_key("purchase_items", ["product_id"])
    _drop_legacy_foreign_key("supplier_payments", ["supplier_id"])
    _drop_legacy_foreign_key("supplier_payments", ["user_id"])
    _drop_legacy_foreign_key("purchase_returns", ["purchase_order_id"])
    _drop_legacy_foreign_key("purchase_returns", ["supplier_id"])
    _drop_legacy_foreign_key("purchase_returns", ["user_id"])
    _drop_legacy_foreign_key("purchase_return_items", ["return_id"])
    _drop_legacy_foreign_key("purchase_return_items", ["product_id"])

    op.create_foreign_key(
        "fk_purchase_orders_supplier_same_tenant",
        "purchase_orders",
        "suppliers",
        ["supplier_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_orders_user_same_tenant",
        "purchase_orders",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_items_order_same_tenant",
        "purchase_items",
        "purchase_orders",
        ["purchase_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_items_product_same_tenant",
        "purchase_items",
        "products",
        ["product_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_supplier_payments_supplier_same_tenant",
        "supplier_payments",
        "suppliers",
        ["supplier_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_supplier_payments_user_same_tenant",
        "supplier_payments",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_returns_order_supplier_same_tenant",
        "purchase_returns",
        "purchase_orders",
        ["purchase_order_id", "supplier_id", "tenant_id"],
        ["id", "supplier_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_returns_supplier_same_tenant",
        "purchase_returns",
        "suppliers",
        ["supplier_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_returns_user_same_tenant",
        "purchase_returns",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_return_items_return_same_tenant",
        "purchase_return_items",
        "purchase_returns",
        ["return_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_return_items_product_same_tenant",
        "purchase_return_items",
        "products",
        ["product_id", "tenant_id"],
        ["id", "tenant_id"],
    )


def downgrade() -> None:
    """Restore the legacy single-column purchase relationships."""
    op.drop_constraint(
        "fk_purchase_return_items_product_same_tenant",
        "purchase_return_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_return_items_return_same_tenant",
        "purchase_return_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_returns_user_same_tenant",
        "purchase_returns",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_returns_supplier_same_tenant",
        "purchase_returns",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_returns_order_supplier_same_tenant",
        "purchase_returns",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_supplier_payments_user_same_tenant",
        "supplier_payments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_supplier_payments_supplier_same_tenant",
        "supplier_payments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_items_product_same_tenant",
        "purchase_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_items_order_same_tenant",
        "purchase_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_orders_user_same_tenant",
        "purchase_orders",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_orders_supplier_same_tenant",
        "purchase_orders",
        type_="foreignkey",
    )

    op.drop_column("purchase_return_items", "tenant_id")
    op.drop_column("purchase_items", "tenant_id")

    op.drop_constraint(
        "uq_purchase_returns_id_tenant_id",
        "purchase_returns",
        type_="unique",
    )
    op.drop_constraint(
        "uq_purchase_orders_id_supplier_tenant_id",
        "purchase_orders",
        type_="unique",
    )
    op.drop_constraint(
        "uq_purchase_orders_id_tenant_id",
        "purchase_orders",
        type_="unique",
    )
    op.drop_constraint("uq_products_id_tenant_id", "products", type_="unique")
    op.drop_constraint("uq_suppliers_id_tenant_id", "suppliers", type_="unique")

    op.create_foreign_key(
        "purchase_orders_supplier_id_fkey",
        "purchase_orders",
        "suppliers",
        ["supplier_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_orders_user_id_fkey",
        "purchase_orders",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_items_purchase_id_fkey",
        "purchase_items",
        "purchase_orders",
        ["purchase_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_items_product_id_fkey",
        "purchase_items",
        "products",
        ["product_id"],
        ["id"],
    )
    op.create_foreign_key(
        "supplier_payments_supplier_id_fkey",
        "supplier_payments",
        "suppliers",
        ["supplier_id"],
        ["id"],
    )
    op.create_foreign_key(
        "supplier_payments_user_id_fkey",
        "supplier_payments",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_returns_purchase_order_id_fkey",
        "purchase_returns",
        "purchase_orders",
        ["purchase_order_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_returns_supplier_id_fkey",
        "purchase_returns",
        "suppliers",
        ["supplier_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_returns_user_id_fkey",
        "purchase_returns",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_return_items_return_id_fkey",
        "purchase_return_items",
        "purchase_returns",
        ["return_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchase_return_items_product_id_fkey",
        "purchase_return_items",
        "products",
        ["product_id"],
        ["id"],
    )
