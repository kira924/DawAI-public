"""enforce expiry and shift integrity

Revision ID: 9f1c2a7d4e60
Revises: 4e8a1c7b2d90
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9f1c2a7d4e60"
down_revision: str | Sequence[str] | None = "4e8a1c7b2d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_safe_shift_history() -> None:
    connection = op.get_bind()
    null_start_times = connection.scalar(
        sa.text("SELECT COUNT(*) FROM shifts WHERE start_time IS NULL")
    )
    duplicate_open_shifts = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT user_id, tenant_id
                FROM shifts
                WHERE status = 'OPEN'
                GROUP BY user_id, tenant_id
                HAVING COUNT(*) > 1
            ) AS duplicate_open
            """
        )
    )
    inconsistent_lifecycle = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM shifts
            WHERE NOT (
                (status = 'OPEN' AND end_time IS NULL
                    AND actual_closing_balance IS NULL AND difference IS NULL)
                OR
                (status = 'CLOSED' AND end_time IS NOT NULL
                    AND actual_closing_balance IS NOT NULL AND difference IS NOT NULL)
            )
            """
        )
    )
    if null_start_times or duplicate_open_shifts or inconsistent_lifecycle:
        raise RuntimeError(
            "Unsafe shift history detected: "
            f"null-start={null_start_times}, "
            f"duplicate-open={duplicate_open_shifts}, "
            f"inconsistent-lifecycle={inconsistent_lifecycle}"
        )


def _assert_safe_downgrade() -> None:
    connection = op.get_bind()
    confirmation_rows = connection.scalar(
        sa.text("SELECT COUNT(*) FROM near_expiry_receipt_confirmations")
    )
    sealed_quarantine_rows = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM sales_return_items
            WHERE condition = 'sealed' AND disposition = 'quarantine'
            """
        )
    )
    if confirmation_rows or sealed_quarantine_rows:
        raise RuntimeError(
            "Downgrade would discard or invalidate expiry evidence: "
            f"confirmations={confirmation_rows}, "
            f"sealed-quarantine={sealed_quarantine_rows}"
        )


def upgrade() -> None:
    _assert_safe_shift_history()

    op.create_index(
        "ix_product_batches_tenant_product_expiry_positive",
        "product_batches",
        ["tenant_id", "product_id", "expiry_date", "id"],
        unique=False,
        postgresql_where=sa.text("quantity > 0"),
    )

    op.drop_constraint(
        "ck_sales_return_items_condition_disposition",
        "sales_return_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sales_return_items_condition_disposition",
        "sales_return_items",
        "(condition = 'sealed' AND disposition IN ('restock', 'quarantine') "
        "AND batch_id IS NOT NULL) "
        "OR (condition IN ('opened_or_damaged', 'unknown') AND disposition = 'quarantine') "
        "OR (condition = 'legacy' AND disposition = 'legacy')",
    )
    op.drop_constraint(
        "ck_quarantine_items_condition_canonical",
        "quarantine_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quarantine_items_condition_canonical",
        "quarantine_items",
        "condition IN ('sealed', 'opened_or_damaged', 'unknown')",
    )
    op.add_column(
        "quarantine_items",
        sa.Column(
            "reason",
            sa.String(length=32),
            nullable=False,
            server_default="return_condition",
        ),
    )
    op.create_check_constraint(
        "ck_quarantine_items_reason_canonical",
        "quarantine_items",
        "reason IN ('expired_batch', 'return_condition')",
    )
    op.create_check_constraint(
        "ck_quarantine_items_reason_context",
        "quarantine_items",
        "(condition = 'sealed' AND reason = 'expired_batch') "
        "OR (condition IN ('opened_or_damaged', 'unknown') "
        "AND reason = 'return_condition')",
    )

    op.create_table(
        "near_expiry_receipt_confirmations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('opening_stock', 'purchase_receipt')",
            name="ck_near_expiry_confirmations_source_canonical",
        ),
        sa.CheckConstraint(
            "(source = 'opening_stock' AND purchase_order_id IS NULL) "
            "OR (source = 'purchase_receipt' AND purchase_order_id IS NOT NULL)",
            name="ck_near_expiry_confirmations_source_context",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_near_expiry_confirmations_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_near_expiry_confirmations_batch_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id", "tenant_id"],
            ["purchase_orders.id", "purchase_orders.tenant_id"],
            name="fk_near_expiry_confirmations_purchase_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_near_expiry_confirmations_user_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_near_expiry_confirmations_tenant_created_at",
        "near_expiry_receipt_confirmations",
        ["tenant_id", "created_at"],
    )

    op.alter_column(
        "shifts",
        "start_time",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.create_check_constraint(
        "ck_shifts_lifecycle_consistent",
        "shifts",
        "(status = 'OPEN' AND end_time IS NULL AND actual_closing_balance IS NULL "
        "AND difference IS NULL) OR "
        "(status = 'CLOSED' AND end_time IS NOT NULL AND actual_closing_balance IS NOT NULL "
        "AND difference IS NOT NULL)",
    )
    op.create_index(
        "uq_shifts_user_open",
        "shifts",
        ["user_id", "tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )


def downgrade() -> None:
    _assert_safe_downgrade()

    op.drop_index("uq_shifts_user_open", table_name="shifts")
    op.drop_constraint("ck_shifts_lifecycle_consistent", "shifts", type_="check")
    op.alter_column(
        "shifts",
        "start_time",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        server_default=None,
    )

    op.drop_index(
        "ix_near_expiry_confirmations_tenant_created_at",
        table_name="near_expiry_receipt_confirmations",
    )
    op.drop_table("near_expiry_receipt_confirmations")

    op.drop_constraint("ck_quarantine_items_reason_context", "quarantine_items", type_="check")
    op.drop_constraint("ck_quarantine_items_reason_canonical", "quarantine_items", type_="check")
    op.drop_column("quarantine_items", "reason")
    op.drop_constraint(
        "ck_quarantine_items_condition_canonical",
        "quarantine_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quarantine_items_condition_canonical",
        "quarantine_items",
        "condition IN ('opened_or_damaged', 'unknown')",
    )
    op.drop_constraint(
        "ck_sales_return_items_condition_disposition",
        "sales_return_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sales_return_items_condition_disposition",
        "sales_return_items",
        "(condition = 'sealed' AND disposition = 'restock' AND batch_id IS NOT NULL) "
        "OR (condition IN ('opened_or_damaged', 'unknown') AND disposition = 'quarantine') "
        "OR (condition = 'legacy' AND disposition = 'legacy')",
    )

    op.drop_index(
        "ix_product_batches_tenant_product_expiry_positive",
        table_name="product_batches",
    )
