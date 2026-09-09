"""add append-only stock movement ledger

Revision ID: 6e1a9d3c4b72
Revises: 5f2b8c4d7a91
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6e1a9d3c4b72"
down_revision: str | Sequence[str] | None = "5f2b8c4d7a91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_existing_stock_is_reconcilable(connection: sa.Connection) -> None:
    mismatch_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM products p "
            "LEFT JOIN ("
            "SELECT product_id, tenant_id, sum(quantity) AS quantity "
            "FROM product_batches GROUP BY product_id, tenant_id"
            ") b ON b.product_id = p.id AND b.tenant_id = p.tenant_id "
            "WHERE p.total_parts <> coalesce(b.quantity, 0)"
        )
    )
    if mismatch_count:
        raise RuntimeError(
            "Cannot initialize the stock ledger while product and batch balances differ"
        )

    invalid_quarantine_count = connection.scalar(
        sa.text("SELECT count(*) FROM quarantine_items WHERE quantity <= 0")
    )
    if invalid_quarantine_count:
        raise RuntimeError(
            "Cannot initialize the stock ledger while quarantine quantities are invalid"
        )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_existing_stock_is_reconcilable(connection)

    op.add_column("invoices", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("invoices", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE invoices SET "
            "idempotency_key = 'legacy-invoice:' || id, "
            "request_fingerprint = repeat('0', 64)"
        )
    )
    op.alter_column(
        "invoices",
        "idempotency_key",
        nullable=False,
        server_default=sa.text("gen_random_uuid()::text"),
    )
    op.alter_column(
        "invoices",
        "request_fingerprint",
        nullable=False,
        server_default=sa.text("repeat('0', 64)"),
    )
    op.create_unique_constraint(
        "uq_invoices_tenant_idempotency_key",
        "invoices",
        ["tenant_id", "idempotency_key"],
    )
    op.create_check_constraint(
        "ck_invoices_idempotency_key_length",
        "invoices",
        "char_length(idempotency_key) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        "ck_invoices_request_fingerprint_length",
        "invoices",
        "char_length(request_fingerprint) = 64",
    )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("stock_bucket", sa.String(length=20), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("source_line_id", sa.BigInteger(), nullable=True),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
            "'opening_stock', 'purchase_receipt', 'sale', 'sales_return_restock', "
            "'sales_return_quarantine', 'purchase_return')",
            name="ck_stock_movements_event_type_canonical",
        ),
        sa.CheckConstraint(
            "stock_bucket IN ('on_hand', 'quarantine')",
            name="ck_stock_movements_bucket_canonical",
        ),
        sa.CheckConstraint(
            "quantity_delta <> 0",
            name="ck_stock_movements_quantity_delta_nonzero",
        ),
        sa.CheckConstraint(
            "char_length(source_key) > 0",
            name="ck_stock_movements_source_key_nonempty",
        ),
        sa.CheckConstraint(
            "(stock_bucket = 'on_hand' AND batch_id IS NOT NULL) OR stock_bucket = 'quarantine'",
            name="ck_stock_movements_batch_context",
        ),
        sa.CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
            "'opening_stock', 'purchase_receipt', 'sales_return_restock', "
            "'sales_return_quarantine') AND quantity_delta > 0) "
            "OR (event_type IN ('sale', 'purchase_return') AND quantity_delta < 0)",
            name="ck_stock_movements_direction_context",
        ),
        sa.CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'opening_stock', "
            "'purchase_receipt', 'sale', 'sales_return_restock', 'purchase_return') "
            "AND stock_bucket = 'on_hand') "
            "OR (event_type IN ('migration_quarantine_balance', "
            "'sales_return_quarantine') AND stock_bucket = 'quarantine')",
            name="ck_stock_movements_event_bucket_context",
        ),
        sa.CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') "
            "AND source_type = 'migration') "
            "OR (event_type = 'opening_stock' AND source_type = 'product') "
            "OR (event_type = 'purchase_receipt' AND source_type = 'purchase_order') "
            "OR (event_type = 'sale' AND source_type = 'invoice') "
            "OR (event_type IN ('sales_return_restock', 'sales_return_quarantine') "
            "AND source_type = 'sales_return') "
            "OR (event_type = 'purchase_return' AND source_type = 'purchase_return')",
            name="ck_stock_movements_source_context",
        ),
        sa.CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') "
            "AND actor_user_id IS NULL AND source_id IS NULL AND source_line_id IS NULL) "
            "OR (event_type NOT IN ('migration_on_hand_balance', "
            "'migration_quarantine_balance') AND actor_user_id IS NOT NULL "
            "AND source_id IS NOT NULL AND source_line_id IS NOT NULL)",
            name="ck_stock_movements_source_identity_required",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stock_movements_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_stock_movements_product_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_stock_movements_batch_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_movements_actor_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_movements"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_key",
            name="uq_stock_movements_tenant_source_key",
        ),
    )
    op.create_index(
        "ix_stock_movements_tenant_created_id",
        "stock_movements",
        ["tenant_id", "created_at", "id"],
    )
    op.create_index(
        "ix_stock_movements_tenant_product_created",
        "stock_movements",
        ["tenant_id", "product_id", "created_at"],
    )
    op.create_index(
        "ix_stock_movements_tenant_batch",
        "stock_movements",
        ["tenant_id", "batch_id"],
    )

    op.execute(
        """
        CREATE FUNCTION validate_stock_movement_source()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') THEN
                RAISE EXCEPTION 'migration stock movements are initialization-only'
                    USING ERRCODE = '55000';
            ELSIF NEW.event_type = 'opening_stock' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM product_batches
                    WHERE id = NEW.batch_id
                      AND product_id = NEW.product_id
                      AND tenant_id = NEW.tenant_id
                      AND quantity = NEW.quantity_delta
                      AND NEW.source_id = NEW.product_id
                      AND NEW.source_line_id = NEW.batch_id
                      AND NEW.source_key = (
                          'opening-stock:product:' || NEW.product_id || ':batch:' || NEW.batch_id
                      )
                ) THEN
                    RAISE EXCEPTION 'opening stock source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'purchase_receipt' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM purchase_items
                    WHERE id = NEW.source_line_id
                      AND purchase_id = NEW.source_id
                      AND product_id = NEW.product_id
                      AND received_batch_id = NEW.batch_id
                      AND tenant_id = NEW.tenant_id
                      AND quantity = NEW.quantity_delta
                      AND NEW.source_key = (
                          'purchase-receipt:item:' || NEW.source_line_id || ':batch:' || NEW.batch_id
                      )
                ) THEN
                    RAISE EXCEPTION 'purchase receipt source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'sale' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM invoice_item_batch_allocations
                    WHERE id = NEW.source_line_id
                      AND invoice_id = NEW.source_id
                      AND product_id = NEW.product_id
                      AND batch_id = NEW.batch_id
                      AND tenant_id = NEW.tenant_id
                      AND quantity = -NEW.quantity_delta
                      AND NEW.source_key = (
                          'sale:allocation:' || NEW.source_line_id || ':batch:' || NEW.batch_id
                      )
                ) THEN
                    RAISE EXCEPTION 'sale source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type IN ('sales_return_restock', 'sales_return_quarantine') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM sales_return_items
                    WHERE id = NEW.source_line_id
                      AND sales_return_id = NEW.source_id
                      AND product_id = NEW.product_id
                      AND batch_id IS NOT DISTINCT FROM NEW.batch_id
                      AND tenant_id = NEW.tenant_id
                      AND quantity = NEW.quantity_delta
                      AND (
                          (
                              NEW.event_type = 'sales_return_restock'
                              AND disposition = 'restock'
                              AND NEW.source_key = (
                                  'sales-return-restock:item:' || NEW.source_line_id
                                  || ':batch:' || NEW.batch_id
                              )
                          )
                          OR (
                              NEW.event_type = 'sales_return_quarantine'
                              AND disposition = 'quarantine'
                              AND NEW.source_key = (
                                  'sales-return-quarantine:item:' || NEW.source_line_id
                              )
                          )
                      )
                ) THEN
                    RAISE EXCEPTION 'sales return source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'purchase_return' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM purchase_return_items
                    WHERE id = NEW.source_line_id
                      AND return_id = NEW.source_id
                      AND product_id = NEW.product_id
                      AND batch_id = NEW.batch_id
                      AND tenant_id = NEW.tenant_id
                      AND quantity = -NEW.quantity_delta
                      AND NEW.source_key = (
                          'purchase-return:item:' || NEW.source_line_id || ':batch:' || NEW.batch_id
                      )
                ) THEN
                    RAISE EXCEPTION 'purchase return source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    connection.execute(
        sa.text(
            "INSERT INTO stock_movements ("
            "event_type, stock_bucket, quantity_delta, source_type, source_key, "
            "product_id, batch_id, tenant_id, created_at"
            ") "
            "SELECT 'migration_on_hand_balance', 'on_hand', quantity, 'migration', "
            "'migration:6e1a9d3c4b72:on-hand:batch:' || id, "
            "product_id, id, tenant_id, now() "
            "FROM product_batches WHERE quantity > 0"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO stock_movements ("
            "event_type, stock_bucket, quantity_delta, source_type, source_key, "
            "product_id, batch_id, tenant_id, created_at"
            ") "
            "SELECT 'migration_quarantine_balance', 'quarantine', quantity, 'migration', "
            "'migration:6e1a9d3c4b72:quarantine:item:' || id, "
            "product_id, batch_id, tenant_id, now() "
            "FROM quarantine_items WHERE quantity > 0"
        )
    )

    op.execute(
        """
        CREATE TRIGGER trg_stock_movements_validate_source
        BEFORE INSERT ON stock_movements
        FOR EACH ROW EXECUTE FUNCTION validate_stock_movement_source()
        """
    )

    op.execute(
        """
        CREATE FUNCTION reject_stock_movement_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'stock_movements is append-only; add a compensating movement'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_movements_append_only
        BEFORE UPDATE OR DELETE ON stock_movements
        FOR EACH ROW EXECUTE FUNCTION reject_stock_movement_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_stock_movements_append_only ON stock_movements")
    op.execute("DROP FUNCTION reject_stock_movement_mutation()")
    op.execute("DROP TRIGGER trg_stock_movements_validate_source ON stock_movements")
    op.execute("DROP FUNCTION validate_stock_movement_source()")
    op.drop_index("ix_stock_movements_tenant_batch", table_name="stock_movements")
    op.drop_index("ix_stock_movements_tenant_product_created", table_name="stock_movements")
    op.drop_index("ix_stock_movements_tenant_created_id", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_constraint(
        "ck_invoices_request_fingerprint_length",
        "invoices",
        type_="check",
    )
    op.drop_constraint(
        "ck_invoices_idempotency_key_length",
        "invoices",
        type_="check",
    )
    op.drop_constraint(
        "uq_invoices_tenant_idempotency_key",
        "invoices",
        type_="unique",
    )
    op.drop_column("invoices", "request_fingerprint")
    op.drop_column("invoices", "idempotency_key")
