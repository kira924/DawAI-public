"""add controlled stock counts and dispositions

Revision ID: 3a7c9e1f5b82
Revises: 6e1a9d3c4b72
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3a7c9e1f5b82"
down_revision: str | Sequence[str] | None = "6e1a9d3c4b72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_stock_movement_constraints() -> None:
    for name in (
        "ck_stock_movements_event_type_canonical",
        "ck_stock_movements_direction_context",
        "ck_stock_movements_event_bucket_context",
        "ck_stock_movements_source_context",
    ):
        op.drop_constraint(name, "stock_movements", type_="check")

    op.create_check_constraint(
        "ck_stock_movements_event_type_canonical",
        "stock_movements",
        "event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
        "'opening_stock', 'purchase_receipt', 'sale', 'sales_return_restock', "
        "'sales_return_quarantine', 'purchase_return', 'stock_adjustment', "
        "'quarantine_transfer_out', 'quarantine_transfer_in', "
        "'quarantine_disposition', 'quarantine_release_out', 'quarantine_release_in')",
    )
    op.create_check_constraint(
        "ck_stock_movements_direction_context",
        "stock_movements",
        "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
        "'opening_stock', 'purchase_receipt', 'sales_return_restock', "
        "'sales_return_quarantine') AND quantity_delta > 0) "
        "OR (event_type IN ('sale', 'purchase_return', 'quarantine_transfer_out', "
        "'quarantine_disposition', 'quarantine_release_out') AND quantity_delta < 0) "
        "OR (event_type = 'stock_adjustment' AND quantity_delta <> 0) "
        "OR (event_type IN ('quarantine_transfer_in', 'quarantine_release_in') "
        "AND quantity_delta > 0)",
    )
    op.create_check_constraint(
        "ck_stock_movements_event_bucket_context",
        "stock_movements",
        "(event_type IN ('migration_on_hand_balance', 'opening_stock', "
        "'purchase_receipt', 'sale', 'sales_return_restock', 'purchase_return', "
        "'stock_adjustment', 'quarantine_transfer_out', 'quarantine_release_in') "
        "AND stock_bucket = 'on_hand') "
        "OR (event_type IN ('migration_quarantine_balance', 'sales_return_quarantine', "
        "'quarantine_transfer_in', 'quarantine_disposition', 'quarantine_release_out') "
        "AND stock_bucket = 'quarantine')",
    )
    op.create_check_constraint(
        "ck_stock_movements_source_context",
        "stock_movements",
        "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') "
        "AND source_type = 'migration') "
        "OR (event_type = 'opening_stock' AND source_type = 'product') "
        "OR (event_type = 'purchase_receipt' AND source_type = 'purchase_order') "
        "OR (event_type = 'sale' AND source_type = 'invoice') "
        "OR (event_type IN ('sales_return_restock', 'sales_return_quarantine') "
        "AND source_type = 'sales_return') "
        "OR (event_type = 'purchase_return' AND source_type = 'purchase_return') "
        "OR (event_type = 'stock_adjustment' AND source_type = 'stock_count') "
        "OR (event_type IN ('quarantine_transfer_out', 'quarantine_transfer_in') "
        "AND source_type = 'quarantine_item') "
        "OR (event_type IN ('quarantine_disposition', 'quarantine_release_out', "
        "'quarantine_release_in') AND source_type = 'quarantine_disposition')",
    )


def _replace_stock_movement_validator() -> None:
    op.get_bind().exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION validate_stock_movement_source()
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
                    WHERE id = NEW.batch_id AND product_id = NEW.product_id
                      AND tenant_id = NEW.tenant_id AND quantity = NEW.quantity_delta
                      AND NEW.source_id = NEW.product_id AND NEW.source_line_id = NEW.batch_id
                      AND NEW.source_key =
                          'opening-stock:product:' || NEW.product_id || ':batch:' || NEW.batch_id
                ) THEN
                    RAISE EXCEPTION 'opening stock source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'purchase_receipt' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM purchase_items
                    WHERE id = NEW.source_line_id AND purchase_id = NEW.source_id
                      AND product_id = NEW.product_id AND received_batch_id = NEW.batch_id
                      AND tenant_id = NEW.tenant_id AND quantity = NEW.quantity_delta
                      AND NEW.source_key =
                          'purchase-receipt:item:' || NEW.source_line_id || ':batch:' || NEW.batch_id
                ) THEN
                    RAISE EXCEPTION 'purchase receipt source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'sale' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM invoice_item_batch_allocations
                    WHERE id = NEW.source_line_id AND invoice_id = NEW.source_id
                      AND product_id = NEW.product_id AND batch_id = NEW.batch_id
                      AND tenant_id = NEW.tenant_id AND quantity = -NEW.quantity_delta
                      AND NEW.source_key =
                          'sale:allocation:' || NEW.source_line_id || ':batch:' || NEW.batch_id
                ) THEN
                    RAISE EXCEPTION 'sale source context is invalid' USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type IN ('sales_return_restock', 'sales_return_quarantine') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM sales_return_items
                    WHERE id = NEW.source_line_id AND sales_return_id = NEW.source_id
                      AND product_id = NEW.product_id
                      AND batch_id IS NOT DISTINCT FROM NEW.batch_id
                      AND tenant_id = NEW.tenant_id AND quantity = NEW.quantity_delta
                      AND (
                          (NEW.event_type = 'sales_return_restock' AND disposition = 'restock'
                           AND NEW.source_key = 'sales-return-restock:item:'
                               || NEW.source_line_id || ':batch:' || NEW.batch_id)
                          OR
                          (NEW.event_type = 'sales_return_quarantine'
                           AND disposition = 'quarantine'
                           AND NEW.source_key =
                               'sales-return-quarantine:item:' || NEW.source_line_id)
                      )
                ) THEN
                    RAISE EXCEPTION 'sales return source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'purchase_return' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM purchase_return_items
                    WHERE id = NEW.source_line_id AND return_id = NEW.source_id
                      AND product_id = NEW.product_id AND batch_id = NEW.batch_id
                      AND tenant_id = NEW.tenant_id AND quantity = -NEW.quantity_delta
                      AND NEW.source_key =
                          'purchase-return:item:' || NEW.source_line_id || ':batch:' || NEW.batch_id
                ) THEN
                    RAISE EXCEPTION 'purchase return source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type = 'stock_adjustment' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM stock_count_items sci
                    JOIN stock_counts sc
                      ON sc.id = sci.stock_count_id AND sc.tenant_id = sci.tenant_id
                    WHERE sci.id = NEW.source_line_id AND sc.id = NEW.source_id
                      AND sci.product_id = NEW.product_id AND sci.batch_id = NEW.batch_id
                      AND sci.tenant_id = NEW.tenant_id AND sc.status = 'applied'
                      AND sci.counted_quantity - sci.expected_quantity = NEW.quantity_delta
                      AND NEW.source_key = 'stock-count:item:' || sci.id
                ) THEN
                    RAISE EXCEPTION 'stock adjustment source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type IN ('quarantine_transfer_out', 'quarantine_transfer_in') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM quarantine_items qi
                    WHERE qi.id = NEW.source_id AND qi.id = NEW.source_line_id
                      AND qi.sales_return_item_id IS NULL
                      AND qi.product_id = NEW.product_id AND qi.batch_id = NEW.batch_id
                      AND qi.tenant_id = NEW.tenant_id
                      AND qi.initial_quantity = abs(NEW.quantity_delta)
                      AND NEW.source_key = 'quarantine-item:' || qi.id ||
                          CASE WHEN NEW.event_type = 'quarantine_transfer_out'
                               THEN ':on-hand-out' ELSE ':quarantine-in' END
                ) THEN
                    RAISE EXCEPTION 'quarantine transfer source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.event_type IN (
                'quarantine_disposition', 'quarantine_release_out', 'quarantine_release_in'
            ) THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM quarantine_dispositions qd
                    JOIN quarantine_items qi
                      ON qi.id = qd.quarantine_item_id
                     AND qi.product_id = qd.product_id AND qi.tenant_id = qd.tenant_id
                    WHERE qd.id = NEW.source_id AND qi.id = NEW.source_line_id
                      AND qd.product_id = NEW.product_id AND qd.batch_id = NEW.batch_id
                      AND qd.tenant_id = NEW.tenant_id
                      AND qd.quantity = abs(NEW.quantity_delta)
                      AND (
                          (NEW.event_type = 'quarantine_disposition'
                           AND qd.action = 'dispose')
                          OR (NEW.event_type IN ('quarantine_release_out',
                                                'quarantine_release_in')
                              AND qd.action = 'release')
                      )
                      AND NEW.source_key = 'quarantine-disposition:' || qd.id ||
                          CASE WHEN NEW.event_type = 'quarantine_release_in'
                               THEN ':on-hand-in' ELSE ':quarantine-out' END
                ) THEN
                    RAISE EXCEPTION 'quarantine disposition source context is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def upgrade() -> None:
    connection = op.get_bind()

    op.create_table(
        "stock_counts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("count_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("applied_by_user_id", sa.Integer(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "count_type IN ('cycle', 'full')", name="ck_stock_counts_type_canonical"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'applied', 'cancelled')",
            name="ck_stock_counts_status_canonical",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_stock_counts_idempotency_key_length",
        ),
        sa.CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_stock_counts_request_fingerprint_length",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND submitted_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND submitted_by_user_id IS NULL "
            "AND applied_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'submitted' AND submitted_at IS NOT NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND submitted_by_user_id IS NOT NULL "
            "AND applied_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'applied' AND submitted_at IS NOT NULL AND applied_at IS NOT NULL "
            "AND cancelled_at IS NULL AND submitted_by_user_id IS NOT NULL "
            "AND applied_by_user_id IS NOT NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'cancelled' AND applied_at IS NULL AND applied_by_user_id IS NULL "
            "AND cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL "
            "AND char_length(cancellation_reason) > 0 "
            "AND ((submitted_at IS NULL AND submitted_by_user_id IS NULL) OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)))",
            name="ck_stock_counts_status_context",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_creator_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_submitter_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_applier_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_canceller_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_stock_counts_id_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_stock_counts_tenant_idempotency_key"
        ),
    )
    op.create_index(
        "ix_stock_counts_tenant_status_created",
        "stock_counts",
        ["tenant_id", "status", "created_at"],
    )
    op.create_table(
        "stock_count_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stock_count_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("expected_quantity", sa.Integer(), nullable=False),
        sa.Column("counted_quantity", sa.Integer(), nullable=False),
        sa.Column("variance_reason", sa.String(length=32), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "expected_quantity >= 0", name="ck_stock_count_items_expected_nonnegative"
        ),
        sa.CheckConstraint(
            "counted_quantity >= 0", name="ck_stock_count_items_counted_nonnegative"
        ),
        sa.CheckConstraint(
            "(expected_quantity = counted_quantity AND variance_reason IS NULL) OR "
            "(expected_quantity <> counted_quantity AND variance_reason IN "
            "('counting_error', 'damage', 'loss', 'found_stock', 'data_correction', 'other'))",
            name="ck_stock_count_items_variance_context",
        ),
        sa.ForeignKeyConstraint(
            ["stock_count_id", "tenant_id"],
            ["stock_counts.id", "stock_counts.tenant_id"],
            name="fk_stock_count_items_count_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_stock_count_items_batch_context",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_count_id", "batch_id", name="uq_stock_count_items_count_batch"),
        sa.UniqueConstraint(
            "id",
            "stock_count_id",
            "product_id",
            "batch_id",
            "tenant_id",
            name="uq_stock_count_items_source_context",
        ),
    )

    op.add_column("quarantine_items", sa.Column("initial_quantity", sa.Integer(), nullable=True))
    op.add_column(
        "quarantine_items",
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
    )
    op.add_column(
        "quarantine_items", sa.Column("idempotency_key", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "quarantine_items", sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
    )
    op.add_column("quarantine_items", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE quarantine_items qi SET "
            "initial_quantity = qi.quantity, "
            "idempotency_key = 'sales-return-item:' || qi.sales_return_item_id, "
            "request_fingerprint = repeat('0', 64), "
            "created_by_user_id = sr.user_id, "
            "created_at = coalesce(qi.created_at, now()) "
            "FROM sales_return_items sri JOIN sales_returns sr ON sr.id = sri.sales_return_id "
            "AND sr.tenant_id = sri.tenant_id "
            "WHERE sri.id = qi.sales_return_item_id AND sri.product_id = qi.product_id "
            "AND sri.tenant_id = qi.tenant_id"
        )
    )
    missing_backfill = connection.scalar(
        sa.text(
            "SELECT count(*) FROM quarantine_items WHERE initial_quantity IS NULL "
            "OR idempotency_key IS NULL OR request_fingerprint IS NULL "
            "OR created_by_user_id IS NULL OR created_at IS NULL"
        )
    )
    if missing_backfill:
        raise RuntimeError("Cannot generalize quarantine items with incomplete source history")

    op.drop_constraint("ck_quarantine_items_quantity_positive", "quarantine_items", type_="check")
    op.drop_constraint("ck_quarantine_items_reason_context", "quarantine_items", type_="check")
    op.drop_constraint("ck_quarantine_items_reason_canonical", "quarantine_items", type_="check")
    op.alter_column("quarantine_items", "sales_return_item_id", nullable=True)
    op.alter_column("quarantine_items", "initial_quantity", nullable=False)
    op.alter_column("quarantine_items", "idempotency_key", nullable=False)
    op.alter_column("quarantine_items", "request_fingerprint", nullable=False)
    op.alter_column("quarantine_items", "created_by_user_id", nullable=False)
    op.alter_column("quarantine_items", "created_at", nullable=False, server_default=sa.func.now())
    op.create_check_constraint(
        "ck_quarantine_items_quantity_bounds",
        "quarantine_items",
        "initial_quantity > 0 AND quantity >= 0 AND quantity <= initial_quantity",
    )
    op.create_check_constraint(
        "ck_quarantine_items_reason_canonical",
        "quarantine_items",
        "reason IN ('expired_batch', 'return_condition', 'damage', 'recall', "
        "'quality_concern', 'other')",
    )
    op.create_check_constraint(
        "ck_quarantine_items_reason_context",
        "quarantine_items",
        "(condition = 'sealed' AND reason = 'expired_batch') "
        "OR (condition IN ('opened_or_damaged', 'unknown') AND reason = 'return_condition') "
        "OR sales_return_item_id IS NULL",
    )
    op.create_check_constraint(
        "ck_quarantine_items_status_context",
        "quarantine_items",
        "(quantity > 0 AND status = 'pending') OR (quantity = 0 AND status = 'resolved')",
    )
    op.create_check_constraint(
        "ck_quarantine_items_idempotency_key_length",
        "quarantine_items",
        "char_length(idempotency_key) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        "ck_quarantine_items_request_fingerprint_length",
        "quarantine_items",
        "char_length(request_fingerprint) = 64",
    )
    op.create_unique_constraint(
        "uq_quarantine_items_context", "quarantine_items", ["id", "product_id", "tenant_id"]
    )
    op.create_unique_constraint(
        "uq_quarantine_items_tenant_idempotency_key",
        "quarantine_items",
        ["tenant_id", "idempotency_key"],
    )
    op.create_foreign_key(
        "fk_quarantine_items_creator_same_tenant",
        "quarantine_items",
        "users",
        ["created_by_user_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "quarantine_dispositions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("quarantine_item_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("performed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('dispose', 'release')",
            name="ck_quarantine_dispositions_action_canonical",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_quarantine_dispositions_quantity_positive"),
        sa.CheckConstraint(
            "char_length(reason) > 0", name="ck_quarantine_dispositions_reason_nonempty"
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_quarantine_dispositions_idempotency_key_length",
        ),
        sa.CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_quarantine_dispositions_request_fingerprint_length",
        ),
        sa.ForeignKeyConstraint(
            ["quarantine_item_id", "product_id", "tenant_id"],
            ["quarantine_items.id", "quarantine_items.product_id", "quarantine_items.tenant_id"],
            name="fk_quarantine_dispositions_item_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_quarantine_dispositions_batch_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["performed_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_quarantine_dispositions_actor_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_quarantine_dispositions_tenant_idempotency_key",
        ),
    )
    op.create_index(
        "ix_quarantine_dispositions_tenant_item_created",
        "quarantine_dispositions",
        ["tenant_id", "quarantine_item_id", "created_at"],
    )

    _replace_stock_movement_constraints()
    _replace_stock_movement_validator()
    op.execute(
        """
        CREATE FUNCTION validate_quarantine_item_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            disposed_quantity bigint;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'quarantine items are append-only records'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.sales_return_item_id IS DISTINCT FROM NEW.sales_return_item_id
               OR OLD.product_id <> NEW.product_id OR OLD.batch_id IS DISTINCT FROM NEW.batch_id
               OR OLD.initial_quantity <> NEW.initial_quantity OR OLD.condition <> NEW.condition
               OR OLD.reason <> NEW.reason OR OLD.tenant_id <> NEW.tenant_id
               OR OLD.idempotency_key <> NEW.idempotency_key
               OR OLD.request_fingerprint <> NEW.request_fingerprint
               OR OLD.created_by_user_id <> NEW.created_by_user_id
               OR OLD.created_at <> NEW.created_at THEN
                RAISE EXCEPTION 'quarantine item identity is immutable' USING ERRCODE = '55000';
            END IF;
            SELECT coalesce(sum(quantity), 0) INTO disposed_quantity
            FROM quarantine_dispositions
            WHERE quarantine_item_id = NEW.id AND tenant_id = NEW.tenant_id;
            IF NEW.quantity <> NEW.initial_quantity - disposed_quantity THEN
                RAISE EXCEPTION 'quarantine quantity requires matching dispositions'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_quarantine_items_controlled_mutation "
        "BEFORE UPDATE OR DELETE ON quarantine_items "
        "FOR EACH ROW EXECUTE FUNCTION validate_quarantine_item_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION validate_stock_count_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            actor_role text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'stock counts are append-only records' USING ERRCODE = '55000';
            END IF;
            IF OLD.id <> NEW.id OR OLD.count_type <> NEW.count_type
               OR OLD.idempotency_key <> NEW.idempotency_key
               OR OLD.request_fingerprint <> NEW.request_fingerprint
               OR OLD.created_by_user_id <> NEW.created_by_user_id
               OR OLD.tenant_id <> NEW.tenant_id OR OLD.created_at <> NEW.created_at THEN
                RAISE EXCEPTION 'stock count identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.status = 'draft' AND NEW.status = 'draft' THEN
                RETURN NEW;
            END IF;
            IF OLD.notes IS DISTINCT FROM NEW.notes THEN
                RAISE EXCEPTION 'stock count notes are immutable after draft'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.status = 'draft' AND NEW.status = 'submitted' THEN
                SELECT role INTO actor_role FROM users
                WHERE id = NEW.submitted_by_user_id AND tenant_id = NEW.tenant_id;
                IF NEW.submitted_by_user_id <> NEW.created_by_user_id
                   AND actor_role IS DISTINCT FROM 'manager' THEN
                    RAISE EXCEPTION 'only the count creator or a manager may submit a stock count'
                        USING ERRCODE = '42501';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status = 'submitted' AND NEW.status = 'applied' THEN
                SELECT role INTO actor_role FROM users
                WHERE id = NEW.applied_by_user_id AND tenant_id = NEW.tenant_id;
                IF actor_role IS DISTINCT FROM 'manager' THEN
                    RAISE EXCEPTION 'only a manager may apply a stock count'
                        USING ERRCODE = '42501';
                END IF;
                IF OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
                   OR OLD.submitted_by_user_id IS DISTINCT FROM NEW.submitted_by_user_id THEN
                    RAISE EXCEPTION 'stock count submission evidence is immutable'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status IN ('draft', 'submitted') AND NEW.status = 'cancelled' THEN
                SELECT role INTO actor_role FROM users
                WHERE id = NEW.cancelled_by_user_id AND tenant_id = NEW.tenant_id;
                IF NEW.cancelled_by_user_id <> NEW.created_by_user_id
                   AND actor_role IS DISTINCT FROM 'manager' THEN
                    RAISE EXCEPTION 'only the count creator or a manager may cancel a stock count'
                        USING ERRCODE = '42501';
                END IF;
                IF OLD.status = 'submitted'
                   AND (OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
                        OR OLD.submitted_by_user_id IS DISTINCT FROM NEW.submitted_by_user_id) THEN
                    RAISE EXCEPTION 'stock count submission evidence is immutable'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'invalid stock count state transition' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_stock_counts_controlled_mutation "
        "BEFORE UPDATE OR DELETE ON stock_counts "
        "FOR EACH ROW EXECUTE FUNCTION validate_stock_count_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION validate_stock_count_item_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            count_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'stock count items cannot be deleted' USING ERRCODE = '55000';
            END IF;
            SELECT status INTO count_status FROM stock_counts
            WHERE id = NEW.stock_count_id AND tenant_id = NEW.tenant_id;
            IF count_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'stock count items are immutable after submission'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'UPDATE'
               AND (OLD.id <> NEW.id OR OLD.stock_count_id <> NEW.stock_count_id
                    OR OLD.product_id <> NEW.product_id OR OLD.batch_id <> NEW.batch_id
                    OR OLD.expected_quantity <> NEW.expected_quantity
                    OR OLD.tenant_id <> NEW.tenant_id) THEN
                RAISE EXCEPTION 'stock count item snapshot identity is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_stock_count_items_controlled_mutation "
        "BEFORE INSERT OR UPDATE OR DELETE ON stock_count_items "
        "FOR EACH ROW EXECUTE FUNCTION validate_stock_count_item_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION validate_quarantine_disposition_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            actor_role text;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                RAISE EXCEPTION 'quarantine dispositions are append-only records'
                    USING ERRCODE = '55000';
            END IF;
            SELECT role INTO actor_role FROM users
            WHERE id = NEW.performed_by_user_id AND tenant_id = NEW.tenant_id;
            IF actor_role IS DISTINCT FROM 'manager' THEN
                RAISE EXCEPTION 'only a manager may record a quarantine disposition'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_quarantine_dispositions_append_only "
        "BEFORE INSERT OR UPDATE OR DELETE ON quarantine_dispositions "
        "FOR EACH ROW EXECUTE FUNCTION validate_quarantine_disposition_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION validate_stock_count_final_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status = 'applied' AND EXISTS (
                SELECT 1 FROM stock_count_items sci
                LEFT JOIN stock_movements sm
                  ON sm.source_type = 'stock_count' AND sm.source_id = NEW.id
                 AND sm.source_line_id = sci.id AND sm.event_type = 'stock_adjustment'
                 AND sm.stock_bucket = 'on_hand'
                 AND sm.quantity_delta = sci.counted_quantity - sci.expected_quantity
                 AND sm.product_id = sci.product_id AND sm.batch_id = sci.batch_id
                 AND sm.tenant_id = sci.tenant_id
                WHERE sci.stock_count_id = NEW.id
                  AND sci.expected_quantity <> sci.counted_quantity AND sm.id IS NULL
            ) THEN
                RAISE EXCEPTION 'applied stock count requires exact adjustment movements'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status <> 'applied' AND EXISTS (
                SELECT 1 FROM stock_movements
                WHERE source_type = 'stock_count' AND source_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'unapplied stock count cannot own adjustment movements'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_stock_counts_final_state "
        "AFTER INSERT OR UPDATE ON stock_counts DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION validate_stock_count_final_state()"
    )
    op.execute(
        """
        CREATE FUNCTION validate_quarantine_disposition_final_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM quarantine_items qi
                WHERE qi.id = NEW.quarantine_item_id AND qi.tenant_id = NEW.tenant_id
                  AND qi.quantity = qi.initial_quantity - (
                      SELECT coalesce(sum(qd.quantity), 0)
                      FROM quarantine_dispositions qd
                      WHERE qd.quarantine_item_id = qi.id AND qd.tenant_id = qi.tenant_id
                  )
            ) OR NOT EXISTS (
                SELECT 1 FROM stock_movements sm
                WHERE sm.source_type = 'quarantine_disposition' AND sm.source_id = NEW.id
                  AND sm.event_type IN ('quarantine_disposition', 'quarantine_release_out')
                  AND sm.stock_bucket = 'quarantine' AND sm.quantity_delta = -NEW.quantity
                  AND sm.product_id = NEW.product_id AND sm.batch_id = NEW.batch_id
                  AND sm.tenant_id = NEW.tenant_id
            ) OR (NEW.action = 'release' AND NOT EXISTS (
                SELECT 1 FROM stock_movements sm
                WHERE sm.source_type = 'quarantine_disposition' AND sm.source_id = NEW.id
                  AND sm.event_type = 'quarantine_release_in'
                  AND sm.stock_bucket = 'on_hand' AND sm.quantity_delta = NEW.quantity
                  AND sm.product_id = NEW.product_id AND sm.batch_id = NEW.batch_id
                  AND sm.tenant_id = NEW.tenant_id
            )) THEN
                RAISE EXCEPTION 'quarantine disposition requires exact balance and movement evidence'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_quarantine_dispositions_final_state "
        "AFTER INSERT ON quarantine_dispositions DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION validate_quarantine_disposition_final_state()"
    )


def downgrade() -> None:
    connection = op.get_bind()
    new_history = connection.scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM stock_counts) "
            "+ (SELECT count(*) FROM quarantine_dispositions) "
            "+ (SELECT count(*) FROM quarantine_items WHERE sales_return_item_id IS NULL)"
        )
    )
    if new_history:
        raise RuntimeError("Cannot downgrade while controlled inventory history exists")

    op.execute("DROP TRIGGER trg_quarantine_dispositions_final_state ON quarantine_dispositions")
    op.execute("DROP FUNCTION validate_quarantine_disposition_final_state()")
    op.execute("DROP TRIGGER trg_stock_counts_final_state ON stock_counts")
    op.execute("DROP FUNCTION validate_stock_count_final_state()")
    op.execute("DROP TRIGGER trg_quarantine_dispositions_append_only ON quarantine_dispositions")
    op.execute("DROP FUNCTION validate_quarantine_disposition_mutation()")
    op.execute("DROP TRIGGER trg_stock_count_items_controlled_mutation ON stock_count_items")
    op.execute("DROP FUNCTION validate_stock_count_item_mutation()")
    op.execute("DROP TRIGGER trg_stock_counts_controlled_mutation ON stock_counts")
    op.execute("DROP FUNCTION validate_stock_count_mutation()")
    op.execute("DROP TRIGGER trg_quarantine_items_controlled_mutation ON quarantine_items")
    op.execute("DROP FUNCTION validate_quarantine_item_mutation()")
    op.drop_index(
        "ix_quarantine_dispositions_tenant_item_created",
        table_name="quarantine_dispositions",
    )
    op.drop_table("quarantine_dispositions")

    op.drop_constraint(
        "fk_quarantine_items_creator_same_tenant", "quarantine_items", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_quarantine_items_tenant_idempotency_key", "quarantine_items", type_="unique"
    )
    op.drop_constraint("uq_quarantine_items_context", "quarantine_items", type_="unique")
    for name in (
        "ck_quarantine_items_request_fingerprint_length",
        "ck_quarantine_items_idempotency_key_length",
        "ck_quarantine_items_status_context",
        "ck_quarantine_items_reason_context",
        "ck_quarantine_items_reason_canonical",
        "ck_quarantine_items_quantity_bounds",
    ):
        op.drop_constraint(name, "quarantine_items", type_="check")
    op.create_check_constraint(
        "ck_quarantine_items_quantity_positive", "quarantine_items", "quantity > 0"
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
        "OR (condition IN ('opened_or_damaged', 'unknown') AND reason = 'return_condition')",
    )
    op.alter_column("quarantine_items", "sales_return_item_id", nullable=False)
    op.alter_column("quarantine_items", "created_at", nullable=True, server_default=None)
    op.drop_column("quarantine_items", "created_by_user_id")
    op.drop_column("quarantine_items", "request_fingerprint")
    op.drop_column("quarantine_items", "idempotency_key")
    op.drop_column("quarantine_items", "status")
    op.drop_column("quarantine_items", "initial_quantity")

    op.drop_table("stock_count_items")
    op.drop_index("ix_stock_counts_tenant_status_created", table_name="stock_counts")
    op.drop_table("stock_counts")

    for name in (
        "ck_stock_movements_event_type_canonical",
        "ck_stock_movements_direction_context",
        "ck_stock_movements_event_bucket_context",
        "ck_stock_movements_source_context",
    ):
        op.drop_constraint(name, "stock_movements", type_="check")
    op.create_check_constraint(
        "ck_stock_movements_event_type_canonical",
        "stock_movements",
        "event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
        "'opening_stock', 'purchase_receipt', 'sale', 'sales_return_restock', "
        "'sales_return_quarantine', 'purchase_return')",
    )
    op.create_check_constraint(
        "ck_stock_movements_direction_context",
        "stock_movements",
        "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
        "'opening_stock', 'purchase_receipt', 'sales_return_restock', "
        "'sales_return_quarantine') AND quantity_delta > 0) "
        "OR (event_type IN ('sale', 'purchase_return') AND quantity_delta < 0)",
    )
    op.create_check_constraint(
        "ck_stock_movements_event_bucket_context",
        "stock_movements",
        "(event_type IN ('migration_on_hand_balance', 'opening_stock', 'purchase_receipt', "
        "'sale', 'sales_return_restock', 'purchase_return') AND stock_bucket = 'on_hand') "
        "OR (event_type IN ('migration_quarantine_balance', 'sales_return_quarantine') "
        "AND stock_bucket = 'quarantine')",
    )
    op.create_check_constraint(
        "ck_stock_movements_source_context",
        "stock_movements",
        "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') "
        "AND source_type = 'migration') "
        "OR (event_type = 'opening_stock' AND source_type = 'product') "
        "OR (event_type = 'purchase_receipt' AND source_type = 'purchase_order') "
        "OR (event_type = 'sale' AND source_type = 'invoice') "
        "OR (event_type IN ('sales_return_restock', 'sales_return_quarantine') "
        "AND source_type = 'sales_return') "
        "OR (event_type = 'purchase_return' AND source_type = 'purchase_return')",
    )
