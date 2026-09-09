"""Enforce exact money and sales return integrity.

Revision ID: 7a3e5c9d1f20
Revises: 2d91b7e4c6f0
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7a3e5c9d1f20"
down_revision: str | Sequence[str] | None = "2d91b7e4c6f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(18, 2)
PERCENTAGE = sa.Numeric(7, 4)

MONEY_COLUMNS = (
    ("products", "price", False),
    ("products", "part_price", True),
    ("purchase_orders", "subtotal", False),
    ("purchase_orders", "total_discount", False),
    ("purchase_orders", "tax_value", False),
    ("purchase_orders", "total_cost", False),
    ("purchase_items", "base_cost", False),
    ("purchase_items", "discount_value", False),
    ("purchase_items", "unit_cost", False),
    ("purchase_items", "subtotal", False),
    ("supplier_payments", "amount", False),
    ("purchase_returns", "total_refund", False),
    ("purchase_return_items", "unit_refund", False),
    ("purchase_return_items", "subtotal", False),
    ("customers", "credit_limit", True),
    ("customers", "total_debt", True),
    ("customer_payments", "amount", False),
    ("expenses", "amount", False),
    ("shifts", "opening_balance", False),
    ("shifts", "expected_closing_balance", False),
    ("shifts", "actual_closing_balance", True),
    ("shifts", "difference", True),
    ("invoices", "total_amount", False),
    ("invoice_items", "unit_price", False),
    ("invoice_items", "subtotal", False),
    ("sales_returns", "total_refund", False),
    ("sales_return_items", "refund_amount", False),
)

PERCENTAGE_COLUMNS = (
    ("purchase_orders", "tax_percentage", False),
    ("purchase_items", "discount_percentage", False),
)

DOMAIN_CHECKS = (
    ("products", "ck_products_price_nonnegative", "price >= 0"),
    (
        "products",
        "ck_products_part_price_nonnegative",
        "part_price IS NULL OR part_price >= 0",
    ),
    ("expenses", "ck_expenses_amount_positive", "amount > 0"),
    ("shifts", "ck_shifts_status_canonical", "status IN ('OPEN', 'CLOSED')"),
    ("shifts", "ck_shifts_opening_balance_nonnegative", "opening_balance >= 0"),
    (
        "shifts",
        "ck_shifts_expected_balance_nonnegative",
        "expected_closing_balance >= 0",
    ),
    (
        "shifts",
        "ck_shifts_actual_balance_nonnegative",
        "actual_closing_balance IS NULL OR actual_closing_balance >= 0",
    ),
    ("purchase_orders", "ck_purchase_orders_subtotal_nonnegative", "subtotal >= 0"),
    (
        "purchase_orders",
        "ck_purchase_orders_discount_nonnegative",
        "total_discount >= 0",
    ),
    (
        "purchase_orders",
        "ck_purchase_orders_tax_percentage_range",
        "tax_percentage >= 0 AND tax_percentage <= 100",
    ),
    (
        "purchase_orders",
        "ck_purchase_orders_tax_value_nonnegative",
        "tax_value >= 0",
    ),
    (
        "purchase_orders",
        "ck_purchase_orders_total_cost_nonnegative",
        "total_cost >= 0",
    ),
    ("purchase_items", "ck_purchase_items_quantity_positive", "quantity > 0"),
    (
        "purchase_items",
        "ck_purchase_items_base_cost_nonnegative",
        "base_cost >= 0",
    ),
    (
        "purchase_items",
        "ck_purchase_items_discount_percentage_range",
        "discount_percentage >= 0 AND discount_percentage <= 100",
    ),
    (
        "purchase_items",
        "ck_purchase_items_discount_value_nonnegative",
        "discount_value >= 0",
    ),
    (
        "purchase_items",
        "ck_purchase_items_unit_cost_nonnegative",
        "unit_cost >= 0",
    ),
    ("purchase_items", "ck_purchase_items_subtotal_nonnegative", "subtotal >= 0"),
    (
        "supplier_payments",
        "ck_supplier_payments_amount_positive",
        "amount > 0",
    ),
    (
        "purchase_returns",
        "ck_purchase_returns_total_refund_nonnegative",
        "total_refund >= 0",
    ),
    (
        "purchase_return_items",
        "ck_purchase_return_items_quantity_positive",
        "quantity > 0",
    ),
    (
        "purchase_return_items",
        "ck_purchase_return_items_unit_refund_nonnegative",
        "unit_refund >= 0",
    ),
    (
        "purchase_return_items",
        "ck_purchase_return_items_subtotal_nonnegative",
        "subtotal >= 0",
    ),
)


def _assert_safe_backfill() -> None:
    connection = op.get_bind()
    checks = {
        "credit returns": """
            SELECT COUNT(*) FROM sales_returns sales_return
            JOIN invoices invoice ON invoice.id=sales_return.invoice_id
            WHERE invoice.payment_type='credit'
        """,
        "customer payments": "SELECT COUNT(*) FROM customer_payments",
        "credit invoices without customers": """
            SELECT COUNT(*) FROM invoices
            WHERE payment_type='credit' AND customer_id IS NULL
        """,
        "customer debt ledger mismatch": """
            SELECT COUNT(*) FROM customers customer
            LEFT JOIN (
                SELECT customer_id, tenant_id,
                       SUM(round(total_amount::numeric, 2)) AS debt
                FROM invoices
                WHERE payment_type='credit'
                GROUP BY customer_id, tenant_id
            ) invoice_debt
              ON invoice_debt.customer_id=customer.id
             AND invoice_debt.tenant_id=customer.tenant_id
            WHERE round(customer.total_debt::numeric, 2) <> COALESCE(invoice_debt.debt, 0)
        """,
        "ambiguous legacy return items": """
            SELECT COUNT(*) FROM sales_return_items return_item
            JOIN sales_returns sales_return ON sales_return.id=return_item.sales_return_id
            WHERE (
                SELECT COUNT(*) FROM invoice_items invoice_item
                WHERE invoice_item.invoice_id=sales_return.invoice_id
                  AND invoice_item.product_id=return_item.product_id
            ) <> 1
        """,
        "invalid invoice totals": "SELECT COUNT(*) FROM invoices WHERE total_amount <= 0",
        "invalid invoice item quantities": "SELECT COUNT(*) FROM invoice_items WHERE quantity <= 0",
        "invalid return item quantities": "SELECT COUNT(*) FROM sales_return_items WHERE quantity <= 0",
        "invoice arithmetic mismatch": """
            SELECT COUNT(*) FROM invoices invoice
            LEFT JOIN (
                SELECT invoice_id,
                       SUM(round(unit_price::numeric, 2) * quantity) AS total
                FROM invoice_items GROUP BY invoice_id
            ) item_total ON item_total.invoice_id=invoice.id
            WHERE round(invoice.total_amount::numeric, 2) <> COALESCE(item_total.total, 0)
        """,
        "sales return arithmetic mismatch": """
            SELECT COUNT(*) FROM sales_returns sales_return
            LEFT JOIN (
                SELECT sales_return_id,
                       SUM(round(refund_amount::numeric, 2)) AS total
                FROM sales_return_items GROUP BY sales_return_id
            ) item_total ON item_total.sales_return_id=sales_return.id
            WHERE round(sales_return.total_refund::numeric, 2) <> COALESCE(item_total.total, 0)
        """,
        "legacy return unit refund mismatch": """
            SELECT COUNT(*) FROM sales_return_items
            WHERE quantity > 0
              AND round(refund_amount::numeric, 2)
                  <> round(refund_amount::numeric / quantity, 2) * quantity
        """,
        "negative customer balances": """
            SELECT COUNT(*) FROM customers WHERE credit_limit < 0 OR total_debt < 0
        """,
        "null customer balances": """
            SELECT COUNT(*) FROM customers WHERE credit_limit IS NULL OR total_debt IS NULL
        """,
        "cumulative return excess": """
            WITH sold AS (
                SELECT invoice_id, product_id, SUM(quantity) AS quantity
                FROM invoice_items GROUP BY invoice_id, product_id
            ), returned AS (
                SELECT sales_return.invoice_id, return_item.product_id,
                       SUM(return_item.quantity) AS quantity
                FROM sales_return_items return_item
                JOIN sales_returns sales_return ON sales_return.id=return_item.sales_return_id
                GROUP BY sales_return.invoice_id, return_item.product_id
            )
            SELECT COUNT(*) FROM returned
            LEFT JOIN sold USING (invoice_id, product_id)
            WHERE returned.quantity > COALESCE(sold.quantity, 0)
        """,
    }
    failures = {name: int(connection.scalar(sa.text(query)) or 0) for name, query in checks.items()}
    for table_name, constraint_name, condition in DOMAIN_CHECKS:
        failures[constraint_name] = int(
            connection.scalar(sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE NOT ({condition})"))
            or 0
        )

    precision_failures: dict[str, int] = {}
    for table_name, column_name, _ in (*MONEY_COLUMNS, *PERCENTAGE_COLUMNS):
        scale = 2 if (table_name, column_name, _) in MONEY_COLUMNS else 4
        query = sa.text(
            f"""
            SELECT COUNT(*) FROM {table_name}
            WHERE {column_name} IS NOT NULL
              AND CASE
                    WHEN {column_name}::text IN ('NaN', 'Infinity', '-Infinity') THEN TRUE
                    ELSE {column_name}::numeric <> round({column_name}::numeric, {scale})
                  END
            """
        )
        precision_failures[f"{table_name}.{column_name}"] = int(connection.scalar(query) or 0)

    unsafe = {name: count for name, count in {**failures, **precision_failures}.items() if count}
    if unsafe:
        summary = ", ".join(f"{name}={count}" for name, count in sorted(unsafe.items()))
        raise RuntimeError(f"Unsafe sales return integrity backfill: {summary}")


def _alter_numeric_columns() -> None:
    for table_name, column_name, nullable in MONEY_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Float(),
            type_=MONEY,
            existing_nullable=nullable,
            postgresql_using=f"round({column_name}::numeric, 2)",
        )
    for table_name, column_name, nullable in PERCENTAGE_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Float(),
            type_=PERCENTAGE,
            existing_nullable=nullable,
            postgresql_using=f"round({column_name}::numeric, 4)",
        )


def _restore_float_columns() -> None:
    for table_name, column_name, nullable in reversed(PERCENTAGE_COLUMNS):
        op.alter_column(
            table_name,
            column_name,
            existing_type=PERCENTAGE,
            type_=sa.Float(),
            existing_nullable=nullable,
            postgresql_using=f"{column_name}::double precision",
        )
    for table_name, column_name, nullable in reversed(MONEY_COLUMNS):
        restored_column = (
            "unit_price"
            if (table_name == "invoice_items" and column_name == "unit_price")
            else column_name
        )
        op.alter_column(
            table_name,
            restored_column,
            existing_type=MONEY,
            type_=sa.Float(),
            existing_nullable=nullable,
            postgresql_using=f"{restored_column}::double precision",
        )


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
    """Install exact-money, allocation, idempotency, and return-integrity controls."""
    _assert_safe_backfill()
    _alter_numeric_columns()
    for table_name, constraint_name, condition in DOMAIN_CHECKS:
        op.create_check_constraint(constraint_name, table_name, condition)

    op.add_column(
        "products",
        sa.Column("return_policy", sa.String(length=32), server_default="standard", nullable=False),
    )
    op.create_check_constraint(
        "ck_products_return_policy_canonical",
        "products",
        "return_policy IN ('standard', 'unopened_only', 'non_returnable')",
    )
    op.create_unique_constraint(
        "uq_product_batches_id_product_tenant_id",
        "product_batches",
        ["id", "product_id", "tenant_id"],
    )

    op.add_column("invoices", sa.Column("outstanding_amount", MONEY, nullable=True))
    op.execute(
        """
        UPDATE invoices
        SET outstanding_amount = CASE WHEN payment_type='credit' THEN total_amount ELSE 0 END
        """
    )
    op.alter_column("invoices", "outstanding_amount", existing_type=MONEY, nullable=False)
    op.create_check_constraint("ck_invoices_total_amount_positive", "invoices", "total_amount > 0")
    op.create_check_constraint(
        "ck_invoices_outstanding_amount_range",
        "invoices",
        "outstanding_amount >= 0 AND outstanding_amount <= total_amount",
    )
    op.create_check_constraint(
        "ck_invoices_payment_outstanding_consistency",
        "invoices",
        "(payment_type = 'cash' AND outstanding_amount = 0) "
        "OR (payment_type = 'credit' AND customer_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_invoices_id_customer_tenant_id",
        "invoices",
        ["id", "customer_id", "tenant_id"],
    )

    op.alter_column("invoice_items", "unit_price", new_column_name="sale_unit_price")
    op.add_column("invoice_items", sa.Column("list_unit_price", MONEY, nullable=True))
    op.execute("UPDATE invoice_items SET list_unit_price=sale_unit_price")
    op.alter_column("invoice_items", "list_unit_price", existing_type=MONEY, nullable=False)
    op.create_check_constraint(
        "ck_invoice_items_quantity_positive", "invoice_items", "quantity > 0"
    )
    op.create_check_constraint(
        "ck_invoice_items_list_price_nonnegative", "invoice_items", "list_unit_price >= 0"
    )
    op.create_check_constraint(
        "ck_invoice_items_sale_price_nonnegative", "invoice_items", "sale_unit_price >= 0"
    )
    op.create_check_constraint(
        "ck_invoice_items_sale_price_not_above_list",
        "invoice_items",
        "sale_unit_price <= list_unit_price",
    )
    op.create_check_constraint(
        "ck_invoice_items_subtotal_nonnegative", "invoice_items", "subtotal >= 0"
    )
    op.create_check_constraint(
        "ck_invoice_items_subtotal_matches_sale_price",
        "invoice_items",
        "subtotal = sale_unit_price * quantity",
    )
    op.create_unique_constraint(
        "uq_invoice_items_identity_context",
        "invoice_items",
        ["id", "invoice_id", "product_id", "tenant_id"],
    )

    op.create_table(
        "invoice_item_batch_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_item_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_invoice_batch_allocations_quantity_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["invoice_item_id", "invoice_id", "product_id", "tenant_id"],
            [
                "invoice_items.id",
                "invoice_items.invoice_id",
                "invoice_items.product_id",
                "invoice_items.tenant_id",
            ],
            name="fk_invoice_batch_allocations_item_context",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_invoice_batch_allocations_batch_context",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_item_id",
            "batch_id",
            name="uq_invoice_batch_allocations_item_batch",
        ),
    )
    op.create_index(
        op.f("ix_invoice_item_batch_allocations_id"),
        "invoice_item_batch_allocations",
        ["id"],
    )

    op.add_column(
        "customers", sa.Column("credit_balance", MONEY, server_default="0", nullable=False)
    )
    op.alter_column(
        "customers",
        "credit_limit",
        existing_type=MONEY,
        nullable=False,
        server_default="0",
    )
    op.alter_column(
        "customers",
        "total_debt",
        existing_type=MONEY,
        nullable=False,
        server_default="0",
    )
    op.create_check_constraint(
        "ck_customers_credit_limit_nonnegative", "customers", "credit_limit >= 0"
    )
    op.create_check_constraint(
        "ck_customers_total_debt_nonnegative", "customers", "total_debt >= 0"
    )
    op.create_check_constraint(
        "ck_customers_credit_balance_nonnegative", "customers", "credit_balance >= 0"
    )

    for columns in (["customer_id"], ["shift_id"], ["user_id"]):
        _drop_legacy_foreign_key("customer_payments", columns)
    op.create_unique_constraint(
        "uq_customer_payments_id_tenant_id", "customer_payments", ["id", "tenant_id"]
    )
    op.create_unique_constraint(
        "uq_customer_payments_id_customer_tenant_id",
        "customer_payments",
        ["id", "customer_id", "tenant_id"],
    )
    op.create_check_constraint(
        "ck_customer_payments_amount_positive", "customer_payments", "amount > 0"
    )
    op.create_foreign_key(
        "fk_customer_payments_customer_same_tenant",
        "customer_payments",
        "customers",
        ["customer_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_customer_payments_shift_user_same_tenant",
        "customer_payments",
        "shifts",
        ["shift_id", "user_id", "tenant_id"],
        ["id", "user_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_customer_payments_user_same_tenant",
        "customer_payments",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )

    op.create_table(
        "customer_payment_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_customer_payment_allocations_amount_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["payment_id", "customer_id", "tenant_id"],
            [
                "customer_payments.id",
                "customer_payments.customer_id",
                "customer_payments.tenant_id",
            ],
            name="fk_customer_payment_allocations_payment_context",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id", "customer_id", "tenant_id"],
            ["invoices.id", "invoices.customer_id", "invoices.tenant_id"],
            name="fk_customer_payment_allocations_invoice_context",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payment_id",
            "invoice_id",
            name="uq_customer_payment_allocations_payment_invoice",
        ),
    )
    op.create_index(
        op.f("ix_customer_payment_allocations_id"),
        "customer_payment_allocations",
        ["id"],
    )

    for name in (
        "idempotency_key",
        "request_fingerprint",
    ):
        op.add_column("sales_returns", sa.Column(name, sa.String(length=64), nullable=True))
    for name in ("cash_refund", "debt_reduction", "credit_balance_increase"):
        op.add_column("sales_returns", sa.Column(name, MONEY, nullable=True))
    op.execute(
        """
        UPDATE sales_returns
        SET idempotency_key='legacy-sales-return-' || id::text,
            request_fingerprint=md5('legacy-sales-return-' || id::text),
            cash_refund=total_refund,
            debt_reduction=0,
            credit_balance_increase=0
        """
    )
    for name in (
        "idempotency_key",
        "request_fingerprint",
        "cash_refund",
        "debt_reduction",
        "credit_balance_increase",
    ):
        op.alter_column("sales_returns", name, nullable=False)
    op.create_check_constraint(
        "ck_sales_returns_total_refund_nonnegative", "sales_returns", "total_refund >= 0"
    )
    op.create_check_constraint(
        "ck_sales_returns_cash_refund_nonnegative", "sales_returns", "cash_refund >= 0"
    )
    op.create_check_constraint(
        "ck_sales_returns_debt_reduction_nonnegative", "sales_returns", "debt_reduction >= 0"
    )
    op.create_check_constraint(
        "ck_sales_returns_credit_balance_increase_nonnegative",
        "sales_returns",
        "credit_balance_increase >= 0",
    )
    op.create_check_constraint(
        "ck_sales_returns_refund_breakdown_matches_total",
        "sales_returns",
        "total_refund = cash_refund + debt_reduction + credit_balance_increase",
    )
    op.create_unique_constraint(
        "uq_sales_returns_id_invoice_tenant_id",
        "sales_returns",
        ["id", "invoice_id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_sales_returns_tenant_idempotency_key",
        "sales_returns",
        ["tenant_id", "idempotency_key"],
    )

    op.drop_constraint(
        "fk_sales_return_items_return_same_tenant", "sales_return_items", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_sales_return_items_product_same_tenant", "sales_return_items", type_="foreignkey"
    )
    op.add_column("sales_return_items", sa.Column("invoice_id", sa.Integer(), nullable=True))
    op.add_column("sales_return_items", sa.Column("invoice_item_id", sa.Integer(), nullable=True))
    op.add_column("sales_return_items", sa.Column("batch_id", sa.Integer(), nullable=True))
    op.add_column("sales_return_items", sa.Column("condition", sa.String(length=32), nullable=True))
    op.add_column(
        "sales_return_items", sa.Column("disposition", sa.String(length=32), nullable=True)
    )
    op.add_column("sales_return_items", sa.Column("unit_refund", MONEY, nullable=True))
    op.execute(
        """
        UPDATE sales_return_items return_item
        SET invoice_id=sales_return.invoice_id,
            invoice_item_id=(
                SELECT invoice_item.id FROM invoice_items invoice_item
                WHERE invoice_item.invoice_id=sales_return.invoice_id
                  AND invoice_item.product_id=return_item.product_id
            ),
            condition='legacy',
            disposition='legacy',
            unit_refund=round(return_item.refund_amount / return_item.quantity, 2)
        FROM sales_returns sales_return
        WHERE sales_return.id=return_item.sales_return_id
        """
    )
    for name in ("invoice_id", "invoice_item_id", "condition", "disposition", "unit_refund"):
        op.alter_column("sales_return_items", name, nullable=False)
    op.create_check_constraint(
        "ck_sales_return_items_quantity_positive", "sales_return_items", "quantity > 0"
    )
    op.create_check_constraint(
        "ck_sales_return_items_unit_refund_nonnegative",
        "sales_return_items",
        "unit_refund >= 0",
    )
    op.create_check_constraint(
        "ck_sales_return_items_refund_nonnegative", "sales_return_items", "refund_amount >= 0"
    )
    op.create_check_constraint(
        "ck_sales_return_items_refund_matches_unit_price",
        "sales_return_items",
        "refund_amount = unit_refund * quantity",
    )
    op.create_check_constraint(
        "ck_sales_return_items_condition_canonical",
        "sales_return_items",
        "condition IN ('sealed', 'opened_or_damaged', 'unknown', 'legacy')",
    )
    op.create_check_constraint(
        "ck_sales_return_items_disposition_canonical",
        "sales_return_items",
        "disposition IN ('restock', 'quarantine', 'legacy')",
    )
    op.create_check_constraint(
        "ck_sales_return_items_condition_disposition",
        "sales_return_items",
        "(condition = 'sealed' AND disposition = 'restock' AND batch_id IS NOT NULL) "
        "OR (condition IN ('opened_or_damaged', 'unknown') AND disposition = 'quarantine') "
        "OR (condition = 'legacy' AND disposition = 'legacy')",
    )
    op.create_unique_constraint(
        "uq_sales_return_items_id_product_tenant_id",
        "sales_return_items",
        ["id", "product_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_return_items_return_context",
        "sales_return_items",
        "sales_returns",
        ["sales_return_id", "invoice_id", "tenant_id"],
        ["id", "invoice_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_return_items_invoice_item_context",
        "sales_return_items",
        "invoice_items",
        ["invoice_item_id", "invoice_id", "product_id", "tenant_id"],
        ["id", "invoice_id", "product_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_sales_return_items_batch_context",
        "sales_return_items",
        "product_batches",
        ["batch_id", "product_id", "tenant_id"],
        ["id", "product_id", "tenant_id"],
    )

    op.create_table(
        "quarantine_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_return_item_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_quarantine_items_quantity_positive"),
        sa.CheckConstraint(
            "condition IN ('opened_or_damaged', 'unknown')",
            name="ck_quarantine_items_condition_canonical",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["sales_return_item_id", "product_id", "tenant_id"],
            [
                "sales_return_items.id",
                "sales_return_items.product_id",
                "sales_return_items.tenant_id",
            ],
            name="fk_quarantine_items_return_item_context",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_quarantine_items_batch_context",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sales_return_item_id", name="uq_quarantine_items_return_item"),
    )
    op.create_index(op.f("ix_quarantine_items_id"), "quarantine_items", ["id"])


def downgrade() -> None:
    """Remove return-integrity structures and restore legacy floating-point columns."""
    op.drop_index(op.f("ix_quarantine_items_id"), table_name="quarantine_items")
    op.drop_table("quarantine_items")

    for constraint_name in (
        "fk_sales_return_items_batch_context",
        "fk_sales_return_items_invoice_item_context",
        "fk_sales_return_items_return_context",
    ):
        op.drop_constraint(constraint_name, "sales_return_items", type_="foreignkey")
    for constraint_name, constraint_type in (
        ("uq_sales_return_items_id_product_tenant_id", "unique"),
        ("ck_sales_return_items_condition_disposition", "check"),
        ("ck_sales_return_items_disposition_canonical", "check"),
        ("ck_sales_return_items_condition_canonical", "check"),
        ("ck_sales_return_items_refund_matches_unit_price", "check"),
        ("ck_sales_return_items_refund_nonnegative", "check"),
        ("ck_sales_return_items_unit_refund_nonnegative", "check"),
        ("ck_sales_return_items_quantity_positive", "check"),
    ):
        op.drop_constraint(constraint_name, "sales_return_items", type_=constraint_type)
    for column_name in (
        "unit_refund",
        "disposition",
        "condition",
        "batch_id",
        "invoice_item_id",
        "invoice_id",
    ):
        op.drop_column("sales_return_items", column_name)
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

    for constraint_name, constraint_type in (
        ("uq_sales_returns_tenant_idempotency_key", "unique"),
        ("uq_sales_returns_id_invoice_tenant_id", "unique"),
        ("ck_sales_returns_refund_breakdown_matches_total", "check"),
        ("ck_sales_returns_credit_balance_increase_nonnegative", "check"),
        ("ck_sales_returns_debt_reduction_nonnegative", "check"),
        ("ck_sales_returns_cash_refund_nonnegative", "check"),
        ("ck_sales_returns_total_refund_nonnegative", "check"),
    ):
        op.drop_constraint(constraint_name, "sales_returns", type_=constraint_type)
    for column_name in (
        "credit_balance_increase",
        "debt_reduction",
        "cash_refund",
        "request_fingerprint",
        "idempotency_key",
    ):
        op.drop_column("sales_returns", column_name)

    op.drop_index(
        op.f("ix_customer_payment_allocations_id"),
        table_name="customer_payment_allocations",
    )
    op.drop_table("customer_payment_allocations")
    for constraint_name in (
        "fk_customer_payments_user_same_tenant",
        "fk_customer_payments_shift_user_same_tenant",
        "fk_customer_payments_customer_same_tenant",
    ):
        op.drop_constraint(constraint_name, "customer_payments", type_="foreignkey")
    op.drop_constraint("ck_customer_payments_amount_positive", "customer_payments", type_="check")
    op.drop_constraint(
        "uq_customer_payments_id_customer_tenant_id",
        "customer_payments",
        type_="unique",
    )
    op.drop_constraint("uq_customer_payments_id_tenant_id", "customer_payments", type_="unique")
    for constraint_name in (
        "ck_customers_credit_balance_nonnegative",
        "ck_customers_total_debt_nonnegative",
        "ck_customers_credit_limit_nonnegative",
    ):
        op.drop_constraint(constraint_name, "customers", type_="check")
    op.drop_column("customers", "credit_balance")
    op.alter_column(
        "customers",
        "total_debt",
        existing_type=MONEY,
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "customers",
        "credit_limit",
        existing_type=MONEY,
        nullable=True,
        server_default=None,
    )
    op.create_foreign_key(
        "customer_payments_customer_id_fkey",
        "customer_payments",
        "customers",
        ["customer_id"],
        ["id"],
    )
    op.create_foreign_key(
        "customer_payments_shift_id_fkey", "customer_payments", "shifts", ["shift_id"], ["id"]
    )
    op.create_foreign_key(
        "customer_payments_user_id_fkey", "customer_payments", "users", ["user_id"], ["id"]
    )

    op.drop_index(
        op.f("ix_invoice_item_batch_allocations_id"),
        table_name="invoice_item_batch_allocations",
    )
    op.drop_table("invoice_item_batch_allocations")
    op.drop_constraint("uq_invoice_items_identity_context", "invoice_items", type_="unique")
    for constraint_name in (
        "ck_invoice_items_subtotal_matches_sale_price",
        "ck_invoice_items_subtotal_nonnegative",
        "ck_invoice_items_sale_price_not_above_list",
        "ck_invoice_items_sale_price_nonnegative",
        "ck_invoice_items_list_price_nonnegative",
        "ck_invoice_items_quantity_positive",
    ):
        op.drop_constraint(constraint_name, "invoice_items", type_="check")
    op.drop_column("invoice_items", "list_unit_price")
    op.alter_column("invoice_items", "sale_unit_price", new_column_name="unit_price")

    op.drop_constraint("uq_invoices_id_customer_tenant_id", "invoices", type_="unique")
    op.drop_constraint("ck_invoices_payment_outstanding_consistency", "invoices", type_="check")
    op.drop_constraint("ck_invoices_outstanding_amount_range", "invoices", type_="check")
    op.drop_constraint("ck_invoices_total_amount_positive", "invoices", type_="check")
    op.drop_column("invoices", "outstanding_amount")

    op.drop_constraint("uq_product_batches_id_product_tenant_id", "product_batches", type_="unique")
    op.drop_constraint("ck_products_return_policy_canonical", "products", type_="check")
    op.drop_column("products", "return_policy")

    for table_name, constraint_name, _ in reversed(DOMAIN_CHECKS):
        op.drop_constraint(constraint_name, table_name, type_="check")

    _restore_float_columns()
