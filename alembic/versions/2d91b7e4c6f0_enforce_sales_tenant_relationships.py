"""Enforce same-tenant relationships throughout sales.

Revision ID: 2d91b7e4c6f0
Revises: 8c4f1d2e6a90
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2d91b7e4c6f0"
down_revision: str | Sequence[str] | None = "8c4f1d2e6a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_existing_relationships_are_tenant_safe() -> None:
    checks = {
        "invoice customer": """
            SELECT COUNT(*) FROM invoices AS invoice
            JOIN customers AS customer ON customer.id = invoice.customer_id
            WHERE customer.tenant_id IS DISTINCT FROM invoice.tenant_id
        """,
        "invoice user": """
            SELECT COUNT(*) FROM invoices AS invoice
            JOIN users AS app_user ON app_user.id = invoice.user_id
            WHERE app_user.tenant_id IS DISTINCT FROM invoice.tenant_id
        """,
        "invoice shift or user": """
            SELECT COUNT(*) FROM invoices AS invoice
            JOIN shifts AS shift ON shift.id = invoice.shift_id
            WHERE shift.tenant_id IS DISTINCT FROM invoice.tenant_id
               OR shift.user_id IS DISTINCT FROM invoice.user_id
        """,
        "invoice item product": """
            SELECT COUNT(*) FROM invoice_items AS invoice_item
            JOIN invoices AS invoice ON invoice.id = invoice_item.invoice_id
            JOIN products AS product ON product.id = invoice_item.product_id
            WHERE product.tenant_id IS DISTINCT FROM invoice.tenant_id
        """,
        "sales return invoice": """
            SELECT COUNT(*) FROM sales_returns AS sales_return
            JOIN invoices AS invoice ON invoice.id = sales_return.invoice_id
            WHERE invoice.tenant_id IS DISTINCT FROM sales_return.tenant_id
        """,
        "sales return shift or user": """
            SELECT COUNT(*) FROM sales_returns AS sales_return
            JOIN shifts AS shift ON shift.id = sales_return.shift_id
            WHERE shift.tenant_id IS DISTINCT FROM sales_return.tenant_id
               OR shift.user_id IS DISTINCT FROM sales_return.user_id
        """,
        "sales return user": """
            SELECT COUNT(*) FROM sales_returns AS sales_return
            JOIN users AS app_user ON app_user.id = sales_return.user_id
            WHERE app_user.tenant_id IS DISTINCT FROM sales_return.tenant_id
        """,
        "sales return item product": """
            SELECT COUNT(*) FROM sales_return_items AS return_item
            JOIN sales_returns AS sales_return
              ON sales_return.id = return_item.sales_return_id
            JOIN products AS product ON product.id = return_item.product_id
            WHERE product.tenant_id IS DISTINCT FROM sales_return.tenant_id
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
        raise RuntimeError(f"Unsafe sales tenant relationships detected: {summary}")


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
    """Fail closed on unsafe rows and add same-tenant sales constraints."""
    _assert_existing_relationships_are_tenant_safe()

    op.create_unique_constraint("uq_customers_id_tenant_id", "customers", ["id", "tenant_id"])
    op.create_unique_constraint(
        "uq_shifts_id_user_id_tenant_id", "shifts", ["id", "user_id", "tenant_id"]
    )
    op.create_unique_constraint("uq_invoices_id_tenant_id", "invoices", ["id", "tenant_id"])
    op.create_unique_constraint(
        "uq_sales_returns_id_tenant_id", "sales_returns", ["id", "tenant_id"]
    )

    op.add_column("invoice_items", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE invoice_items AS invoice_item
        SET tenant_id = invoice.tenant_id
        FROM invoices AS invoice
        WHERE invoice.id = invoice_item.invoice_id
        """
    )
    op.alter_column("invoice_items", "tenant_id", existing_type=sa.Integer(), nullable=False)

    op.add_column("sales_return_items", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE sales_return_items AS return_item
        SET tenant_id = sales_return.tenant_id
        FROM sales_returns AS sales_return
        WHERE sales_return.id = return_item.sales_return_id
        """
    )
    op.alter_column("sales_return_items", "tenant_id", existing_type=sa.Integer(), nullable=False)

    for table_name, columns in (
        ("invoices", ["customer_id"]),
        ("invoices", ["user_id"]),
        ("invoices", ["shift_id"]),
        ("invoice_items", ["invoice_id"]),
        ("invoice_items", ["product_id"]),
        ("sales_returns", ["invoice_id"]),
        ("sales_returns", ["shift_id"]),
        ("sales_returns", ["user_id"]),
        ("sales_return_items", ["sales_return_id"]),
        ("sales_return_items", ["product_id"]),
    ):
        _drop_legacy_foreign_key(table_name, columns)

    op.create_foreign_key(
        "fk_invoices_customer_same_tenant",
        "invoices",
        "customers",
        ["customer_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_invoices_user_same_tenant",
        "invoices",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_invoices_shift_user_same_tenant",
        "invoices",
        "shifts",
        ["shift_id", "user_id", "tenant_id"],
        ["id", "user_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_invoice_items_invoice_same_tenant",
        "invoice_items",
        "invoices",
        ["invoice_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_invoice_items_product_same_tenant",
        "invoice_items",
        "products",
        ["product_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_returns_invoice_same_tenant",
        "sales_returns",
        "invoices",
        ["invoice_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_returns_shift_user_same_tenant",
        "sales_returns",
        "shifts",
        ["shift_id", "user_id", "tenant_id"],
        ["id", "user_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_returns_user_same_tenant",
        "sales_returns",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_return_items_return_same_tenant",
        "sales_return_items",
        "sales_returns",
        ["sales_return_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_return_items_product_same_tenant",
        "sales_return_items",
        "products",
        ["product_id", "tenant_id"],
        ["id", "tenant_id"],
    )


def downgrade() -> None:
    """Restore legacy single-column sales relationships."""
    for table_name, constraint_name in (
        ("sales_return_items", "fk_sales_return_items_product_same_tenant"),
        ("sales_return_items", "fk_sales_return_items_return_same_tenant"),
        ("sales_returns", "fk_sales_returns_user_same_tenant"),
        ("sales_returns", "fk_sales_returns_shift_user_same_tenant"),
        ("sales_returns", "fk_sales_returns_invoice_same_tenant"),
        ("invoice_items", "fk_invoice_items_product_same_tenant"),
        ("invoice_items", "fk_invoice_items_invoice_same_tenant"),
        ("invoices", "fk_invoices_shift_user_same_tenant"),
        ("invoices", "fk_invoices_user_same_tenant"),
        ("invoices", "fk_invoices_customer_same_tenant"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    op.drop_column("sales_return_items", "tenant_id")
    op.drop_column("invoice_items", "tenant_id")
    op.drop_constraint("uq_sales_returns_id_tenant_id", "sales_returns", type_="unique")
    op.drop_constraint("uq_invoices_id_tenant_id", "invoices", type_="unique")
    op.drop_constraint("uq_shifts_id_user_id_tenant_id", "shifts", type_="unique")
    op.drop_constraint("uq_customers_id_tenant_id", "customers", type_="unique")

    for table_name, constraint_name, source_column, target_table in (
        ("invoices", "invoices_customer_id_fkey", "customer_id", "customers"),
        ("invoices", "invoices_user_id_fkey", "user_id", "users"),
        ("invoices", "invoices_shift_id_fkey", "shift_id", "shifts"),
        ("invoice_items", "invoice_items_invoice_id_fkey", "invoice_id", "invoices"),
        ("invoice_items", "invoice_items_product_id_fkey", "product_id", "products"),
        ("sales_returns", "sales_returns_invoice_id_fkey", "invoice_id", "invoices"),
        ("sales_returns", "sales_returns_shift_id_fkey", "shift_id", "shifts"),
        ("sales_returns", "sales_returns_user_id_fkey", "user_id", "users"),
        (
            "sales_return_items",
            "sales_return_items_sales_return_id_fkey",
            "sales_return_id",
            "sales_returns",
        ),
        (
            "sales_return_items",
            "sales_return_items_product_id_fkey",
            "product_id",
            "products",
        ),
    ):
        op.create_foreign_key(constraint_name, table_name, target_table, [source_column], ["id"])
