"""add general financial and cash ledger

Revision ID: c4d8e2f6a190
Revises: 3a7c9e1f5b82
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d8e2f6a190"
down_revision: str | Sequence[str] | None = "3a7c9e1f5b82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_existing_finances_reconcile(connection: sa.Connection) -> None:
    customer_mismatches = connection.scalar(
        sa.text(
            "SELECT count(*) FROM customers c LEFT JOIN ("
            "SELECT customer_id, tenant_id, sum(outstanding_amount) amount "
            "FROM invoices WHERE payment_type = 'credit' GROUP BY customer_id, tenant_id"
            ") i ON i.customer_id = c.id AND i.tenant_id = c.tenant_id "
            "WHERE c.total_debt <> coalesce(i.amount, 0)"
        )
    )
    if customer_mismatches:
        raise RuntimeError("Cannot initialize the financial ledger while customer debt differs")

    customer_credit_mismatches = connection.scalar(
        sa.text(
            "SELECT count(*) FROM customers c LEFT JOIN ("
            "SELECT i.customer_id, sr.tenant_id, sum(sr.credit_balance_increase) amount "
            "FROM sales_returns sr JOIN invoices i ON i.id = sr.invoice_id "
            "WHERE i.customer_id IS NOT NULL GROUP BY i.customer_id, sr.tenant_id"
            ") r ON r.customer_id = c.id AND r.tenant_id = c.tenant_id "
            "WHERE c.credit_balance <> coalesce(r.amount, 0)"
        )
    )
    if customer_credit_mismatches:
        raise RuntimeError("Cannot initialize the financial ledger while customer credit differs")

    shift_mismatches = connection.scalar(
        sa.text(
            "SELECT count(*) FROM shifts s WHERE s.status = 'CLOSED' AND "
            "s.expected_closing_balance <> round(s.opening_balance "
            "+ coalesce((SELECT sum(i.total_amount) FROM invoices i WHERE i.shift_id = s.id "
            "AND i.tenant_id = s.tenant_id AND i.payment_type = 'cash'), 0) "
            "+ coalesce((SELECT sum(cp.amount) FROM customer_payments cp WHERE cp.shift_id = s.id "
            "AND cp.tenant_id = s.tenant_id), 0) "
            "- coalesce((SELECT sum(e.amount) FROM expenses e WHERE e.shift_id = s.id "
            "AND e.tenant_id = s.tenant_id), 0) "
            "- coalesce((SELECT sum(sr.cash_refund) FROM sales_returns sr WHERE sr.shift_id = s.id "
            "AND sr.tenant_id = s.tenant_id), 0), 2)"
        )
    )
    if shift_mismatches:
        raise RuntimeError("Cannot initialize the financial ledger while shift cash differs")


def _create_source_validation() -> None:
    op.execute(
        """
        CREATE FUNCTION validate_financial_transaction_source()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            tx financial_transactions%ROWTYPE;
            transaction_identity bigint;
            transaction_tenant integer;
            source_amount numeric(18,2);
            expected_lines integer;
        BEGIN
            IF TG_TABLE_NAME = 'financial_entries' THEN
                transaction_identity := NEW.transaction_id;
                transaction_tenant := NEW.tenant_id;
            ELSE
                transaction_identity := NEW.id;
                transaction_tenant := NEW.tenant_id;
            END IF;
            SELECT * INTO tx FROM financial_transactions
            WHERE id = transaction_identity AND tenant_id = transaction_tenant;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM financial_entries e
                WHERE e.transaction_id = tx.id AND e.tenant_id = tx.tenant_id
                GROUP BY e.transaction_id
                HAVING sum(CASE WHEN e.entry_side = 'debit' THEN e.amount ELSE 0 END)
                     = sum(CASE WHEN e.entry_side = 'credit' THEN e.amount ELSE 0 END)
                   AND count(*) >= 2
            ) THEN
                RAISE EXCEPTION 'financial transaction must be balanced and non-empty'
                    USING ERRCODE = '23514';
            END IF;

            IF tx.event_type = 'sale' THEN
                SELECT i.total_amount INTO source_amount FROM invoices i
                WHERE i.id = tx.source_id AND i.tenant_id = tx.tenant_id
                  AND i.user_id = tx.actor_user_id
                  AND tx.source_key = 'invoice:' || i.id
                  AND tx.customer_id IS NOT DISTINCT FROM i.customer_id
                  AND tx.shift_id IS NOT DISTINCT FROM (
                      CASE WHEN i.payment_type = 'cash' THEN i.shift_id ELSE NULL END
                  );
                IF source_amount IS NULL OR NOT EXISTS (
                    SELECT 1 FROM invoices i WHERE i.id = tx.source_id
                      AND ((i.payment_type = 'cash' AND EXISTS (
                          SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                          AND e.account_code = 'cash_drawer' AND e.entry_side = 'debit'
                          AND e.amount = source_amount
                      )) OR (i.payment_type = 'credit' AND EXISTS (
                          SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                          AND e.account_code = 'customer_receivable' AND e.entry_side = 'debit'
                          AND e.amount = source_amount
                      )))
                ) OR NOT EXISTS (
                    SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                    AND e.account_code = 'sales_revenue' AND e.entry_side = 'credit'
                    AND e.amount = source_amount
                ) OR (SELECT count(*) FROM financial_entries e WHERE e.transaction_id = tx.id) <> 2
                THEN RAISE EXCEPTION 'sale financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'sales_return' THEN
                SELECT sr.total_refund,
                       1 + (sr.cash_refund > 0)::int + (sr.debt_reduction > 0)::int
                         + (sr.credit_balance_increase > 0)::int
                INTO source_amount, expected_lines
                FROM sales_returns sr JOIN invoices i ON i.id = sr.invoice_id
                WHERE sr.id = tx.source_id AND sr.tenant_id = tx.tenant_id
                  AND sr.user_id = tx.actor_user_id
                  AND tx.source_key = 'sales-return:' || sr.id
                  AND tx.customer_id IS NOT DISTINCT FROM i.customer_id
                  AND tx.shift_id IS NOT DISTINCT FROM (
                      CASE WHEN sr.cash_refund > 0 THEN sr.shift_id ELSE NULL END
                  );
                IF source_amount IS NULL OR NOT EXISTS (
                    SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                    AND e.account_code = 'sales_returns' AND e.entry_side = 'debit'
                    AND e.amount = source_amount
                ) OR (SELECT count(*) FROM financial_entries e WHERE e.transaction_id = tx.id)
                     <> expected_lines
                OR EXISTS (
                    SELECT 1 FROM sales_returns sr WHERE sr.id = tx.source_id AND (
                        (sr.cash_refund > 0 AND NOT EXISTS (SELECT 1 FROM financial_entries e
                            WHERE e.transaction_id = tx.id AND e.account_code = 'cash_drawer'
                            AND e.entry_side = 'credit' AND e.amount = sr.cash_refund)) OR
                        (sr.debt_reduction > 0 AND NOT EXISTS (SELECT 1 FROM financial_entries e
                            WHERE e.transaction_id = tx.id AND e.account_code = 'customer_receivable'
                            AND e.entry_side = 'credit' AND e.amount = sr.debt_reduction)) OR
                        (sr.credit_balance_increase > 0 AND NOT EXISTS (SELECT 1 FROM financial_entries e
                            WHERE e.transaction_id = tx.id AND e.account_code = 'customer_credit'
                            AND e.entry_side = 'credit' AND e.amount = sr.credit_balance_increase))
                    )
                ) THEN RAISE EXCEPTION 'sales return financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'customer_payment' THEN
                SELECT cp.amount INTO source_amount FROM customer_payments cp
                WHERE cp.id = tx.source_id AND cp.tenant_id = tx.tenant_id
                  AND cp.user_id = tx.actor_user_id AND cp.shift_id = tx.shift_id
                  AND cp.customer_id = tx.customer_id
                  AND tx.source_key = 'customer-payment:' || cp.id;
                IF source_amount IS NULL OR (SELECT count(*) FROM financial_entries e
                    WHERE e.transaction_id = tx.id) <> 2 OR NOT EXISTS (
                    SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                    AND e.account_code = 'cash_drawer' AND e.entry_side = 'debit'
                    AND e.amount = source_amount
                ) OR NOT EXISTS (
                    SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                    AND e.account_code = 'customer_receivable' AND e.entry_side = 'credit'
                    AND e.amount = source_amount
                ) THEN RAISE EXCEPTION 'customer payment financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'purchase_receipt' THEN
                SELECT po.total_cost INTO source_amount FROM purchase_orders po
                WHERE po.id = tx.source_id AND po.tenant_id = tx.tenant_id
                  AND po.status = 'RECEIVED' AND po.user_id = tx.actor_user_id
                  AND po.supplier_id = tx.supplier_id AND tx.shift_id IS NULL
                  AND tx.source_key = 'purchase-receipt:order:' || po.id;
                IF source_amount IS NULL OR source_amount <= 0 OR
                   (SELECT count(*) FROM financial_entries e WHERE e.transaction_id = tx.id) <> 2 OR
                   NOT EXISTS (SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                     AND e.account_code = 'inventory_purchases' AND e.entry_side = 'debit'
                     AND e.amount = source_amount) OR
                   NOT EXISTS (SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                     AND e.account_code = 'supplier_payable' AND e.entry_side = 'credit'
                     AND e.amount = source_amount)
                THEN RAISE EXCEPTION 'purchase receipt financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'purchase_return' THEN
                SELECT pr.total_refund INTO source_amount FROM purchase_returns pr
                WHERE pr.id = tx.source_id AND pr.tenant_id = tx.tenant_id
                  AND pr.user_id = tx.actor_user_id AND pr.supplier_id = tx.supplier_id
                  AND tx.shift_id IS NULL AND tx.source_key = 'purchase-return:' || pr.id;
                IF source_amount IS NULL OR source_amount <= 0 OR
                   (SELECT count(*) FROM financial_entries e WHERE e.transaction_id = tx.id) <> 2 OR
                   NOT EXISTS (SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                     AND e.account_code = 'supplier_payable' AND e.entry_side = 'debit'
                     AND e.amount = source_amount) OR
                   NOT EXISTS (SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                     AND e.account_code = 'inventory_purchases' AND e.entry_side = 'credit'
                     AND e.amount = source_amount)
                THEN RAISE EXCEPTION 'purchase return financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'supplier_payment' THEN
                SELECT sp.amount INTO source_amount FROM supplier_payments sp
                WHERE sp.id = tx.source_id AND sp.tenant_id = tx.tenant_id
                  AND sp.user_id = tx.actor_user_id AND sp.supplier_id = tx.supplier_id
                  AND tx.shift_id IS NOT DISTINCT FROM sp.shift_id
                  AND tx.source_key = 'supplier-payment:' || sp.id;
                IF source_amount IS NULL OR (SELECT count(*) FROM financial_entries e
                    WHERE e.transaction_id = tx.id) <> 2 OR NOT EXISTS (
                    SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                    AND e.account_code = 'supplier_payable' AND e.entry_side = 'debit'
                    AND e.amount = source_amount
                ) OR NOT EXISTS (
                    SELECT 1 FROM supplier_payments sp WHERE sp.id = tx.source_id AND EXISTS (
                        SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                        AND e.account_code = CASE WHEN sp.payment_channel = 'cash'
                            THEN 'cash_drawer' ELSE 'external_funds' END
                        AND e.entry_side = 'credit' AND e.amount = source_amount
                    )
                ) THEN RAISE EXCEPTION 'supplier payment financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'expense' THEN
                SELECT e.amount INTO source_amount FROM expenses e
                WHERE e.id = tx.source_id AND e.tenant_id = tx.tenant_id
                  AND e.user_id = tx.actor_user_id AND e.shift_id = tx.shift_id
                  AND tx.source_key = 'expense:' || e.id;
                IF source_amount IS NULL OR (SELECT count(*) FROM financial_entries fe
                    WHERE fe.transaction_id = tx.id) <> 2 OR NOT EXISTS (
                    SELECT 1 FROM financial_entries fe WHERE fe.transaction_id = tx.id
                    AND fe.account_code = 'operating_expense' AND fe.entry_side = 'debit'
                    AND fe.amount = source_amount
                ) OR NOT EXISTS (
                    SELECT 1 FROM financial_entries fe WHERE fe.transaction_id = tx.id
                    AND fe.account_code = 'cash_drawer' AND fe.entry_side = 'credit'
                    AND fe.amount = source_amount
                ) THEN RAISE EXCEPTION 'expense financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;

            ELSIF tx.event_type = 'shift_adjustment' THEN
                SELECT abs(s.difference) INTO source_amount FROM shifts s
                WHERE s.id = tx.source_id AND s.tenant_id = tx.tenant_id
                  AND s.user_id = tx.actor_user_id AND s.id = tx.shift_id
                  AND s.status = 'CLOSED' AND s.difference <> 0
                  AND tx.source_key = 'shift-adjustment:' || s.id;
                IF source_amount IS NULL OR (SELECT count(*) FROM financial_entries e
                    WHERE e.transaction_id = tx.id) <> 2 OR NOT EXISTS (
                    SELECT 1 FROM shifts s WHERE s.id = tx.source_id AND (
                        (s.difference > 0 AND EXISTS (SELECT 1 FROM financial_entries e
                            WHERE e.transaction_id = tx.id AND e.account_code = 'cash_drawer'
                            AND e.entry_side = 'debit' AND e.amount = source_amount) AND EXISTS (
                            SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                            AND e.account_code = 'cash_over_short' AND e.entry_side = 'credit'
                            AND e.amount = source_amount)) OR
                        (s.difference < 0 AND EXISTS (SELECT 1 FROM financial_entries e
                            WHERE e.transaction_id = tx.id AND e.account_code = 'cash_over_short'
                            AND e.entry_side = 'debit' AND e.amount = source_amount) AND EXISTS (
                            SELECT 1 FROM financial_entries e WHERE e.transaction_id = tx.id
                            AND e.account_code = 'cash_drawer' AND e.entry_side = 'credit'
                            AND e.amount = source_amount))
                    )
                ) THEN RAISE EXCEPTION 'shift adjustment financial source context is invalid'
                    USING ERRCODE = '23514'; END IF;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_existing_finances_reconcile(connection)

    op.add_column("customer_payments", sa.Column("idempotency_key", sa.String(64)))
    op.add_column("customer_payments", sa.Column("request_fingerprint", sa.String(64)))
    op.add_column("expenses", sa.Column("idempotency_key", sa.String(64)))
    op.add_column("expenses", sa.Column("request_fingerprint", sa.String(64)))
    op.add_column(
        "supplier_payments",
        sa.Column("payment_channel", sa.String(16), server_default="external", nullable=False),
    )
    op.add_column("supplier_payments", sa.Column("shift_id", sa.Integer()))

    connection.execute(
        sa.text(
            "UPDATE customer_payments SET idempotency_key = 'legacy-customer-payment:' || id, "
            "request_fingerprint = repeat('0', 64); "
            "UPDATE expenses SET idempotency_key = 'legacy-expense:' || id, "
            "request_fingerprint = repeat('0', 64)"
        )
    )
    for table in ("customer_payments", "expenses"):
        op.alter_column(
            table,
            "idempotency_key",
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        )
        op.alter_column(
            table,
            "request_fingerprint",
            nullable=False,
            server_default=sa.text("repeat('0', 64)"),
        )
        op.create_unique_constraint(
            f"uq_{table}_tenant_idempotency_key", table, ["tenant_id", "idempotency_key"]
        )
        op.create_check_constraint(
            f"ck_{table}_idempotency_key_length",
            table,
            "char_length(idempotency_key) BETWEEN 1 AND 64",
        )
        op.create_check_constraint(
            f"ck_{table}_request_fingerprint_length",
            table,
            "char_length(request_fingerprint) = 64",
        )
    op.create_unique_constraint("uq_expenses_id_tenant_id", "expenses", ["id", "tenant_id"])
    op.create_check_constraint(
        "ck_supplier_payments_channel_canonical",
        "supplier_payments",
        "payment_channel IN ('cash', 'external')",
    )
    op.create_check_constraint(
        "ck_supplier_payments_channel_shift_context",
        "supplier_payments",
        "(payment_channel = 'cash' AND shift_id IS NOT NULL) OR "
        "(payment_channel = 'external' AND shift_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_supplier_payments_shift_user_same_tenant",
        "supplier_payments",
        "shifts",
        ["shift_id", "user_id", "tenant_id"],
        ["id", "user_id", "tenant_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "financial_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(160), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("shift_id", sa.Integer()),
        sa.Column("customer_id", sa.Integer()),
        sa.Column("supplier_id", sa.Integer()),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event_type IN ('sale', 'sales_return', 'customer_payment', 'purchase_receipt', "
            "'purchase_return', 'supplier_payment', 'expense', 'shift_adjustment')",
            name="ck_financial_transactions_event_type_canonical",
        ),
        sa.CheckConstraint(
            "source_type IN ('invoice', 'sales_return', 'customer_payment', 'purchase_order', "
            "'purchase_return', 'supplier_payment', 'expense', 'shift')",
            name="ck_financial_transactions_source_type_canonical",
        ),
        sa.CheckConstraint(
            "char_length(source_key) BETWEEN 1 AND 160",
            name="ck_financial_transactions_source_key_length",
        ),
        sa.CheckConstraint(
            "(event_type = 'sale' AND source_type = 'invoice') OR "
            "(event_type = 'sales_return' AND source_type = 'sales_return') OR "
            "(event_type = 'customer_payment' AND source_type = 'customer_payment') OR "
            "(event_type = 'purchase_receipt' AND source_type = 'purchase_order') OR "
            "(event_type = 'purchase_return' AND source_type = 'purchase_return') OR "
            "(event_type = 'supplier_payment' AND source_type = 'supplier_payment') OR "
            "(event_type = 'expense' AND source_type = 'expense') OR "
            "(event_type = 'shift_adjustment' AND source_type = 'shift')",
            name="ck_financial_transactions_event_source_context",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_financial_transactions"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_financial_transactions_id_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_key", name="uq_financial_transactions_tenant_source_key"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_financial_transactions_actor_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shift_id", "actor_user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_financial_transactions_shift_actor_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["customers.id", "customers.tenant_id"],
            name="fk_financial_transactions_customer_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id", "tenant_id"],
            ["suppliers.id", "suppliers.tenant_id"],
            name="fk_financial_transactions_supplier_same_tenant",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_financial_transactions_tenant_occurred_id",
        "financial_transactions",
        ["tenant_id", "recorded_at", "id"],
    )
    op.create_index(
        "ix_financial_transactions_tenant_shift",
        "financial_transactions",
        ["tenant_id", "shift_id"],
    )
    op.create_index(
        "ix_financial_transactions_tenant_customer",
        "financial_transactions",
        ["tenant_id", "customer_id"],
    )
    op.create_index(
        "ix_financial_transactions_tenant_supplier",
        "financial_transactions",
        ["tenant_id", "supplier_id"],
    )
    op.create_table(
        "financial_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column("account_code", sa.String(40), nullable=False),
        sa.Column("entry_side", sa.String(8), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "account_code IN ('cash_drawer', 'customer_receivable', 'customer_credit', "
            "'supplier_payable', 'sales_revenue', 'sales_returns', 'inventory_purchases', "
            "'operating_expense', 'external_funds', 'cash_over_short')",
            name="ck_financial_entries_account_canonical",
        ),
        sa.CheckConstraint(
            "entry_side IN ('debit', 'credit')", name="ck_financial_entries_side_canonical"
        ),
        sa.CheckConstraint("amount > 0", name="ck_financial_entries_amount_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_financial_entries"),
        sa.UniqueConstraint(
            "transaction_id",
            "account_code",
            "entry_side",
            name="uq_financial_entries_transaction_account_side",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "tenant_id"],
            ["financial_transactions.id", "financial_transactions.tenant_id"],
            name="fk_financial_entries_transaction_same_tenant",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_financial_entries_tenant_account",
        "financial_entries",
        ["tenant_id", "account_code"],
    )

    _create_source_validation()
    op.execute(
        """
        CREATE FUNCTION reject_financial_ledger_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'financial ledger is append-only; record a reversal instead'
                USING ERRCODE = '55000';
        END; $$;
        CREATE TRIGGER trg_financial_transactions_append_only
        BEFORE UPDATE OR DELETE ON financial_transactions
        FOR EACH ROW EXECUTE FUNCTION reject_financial_ledger_mutation();
        CREATE TRIGGER trg_financial_entries_append_only
        BEFORE UPDATE OR DELETE ON financial_entries
        FOR EACH ROW EXECUTE FUNCTION reject_financial_ledger_mutation();
        CREATE CONSTRAINT TRIGGER trg_financial_transaction_source
        AFTER INSERT ON financial_transactions DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_financial_transaction_source();
        CREATE CONSTRAINT TRIGGER trg_financial_entry_source
        AFTER INSERT ON financial_entries DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_financial_transaction_source();
        """
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'sale', 'invoice', i.id, 'invoice:' || i.id, i.user_id,
                   CASE WHEN i.payment_type = 'cash' THEN i.shift_id END,
                   i.customer_id, NULL, i.tenant_id, i.created_at
            FROM invoices i;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'sales_return', 'sales_return', sr.id, 'sales-return:' || sr.id, sr.user_id,
                   CASE WHEN sr.cash_refund > 0 THEN sr.shift_id END,
                   i.customer_id, NULL, sr.tenant_id, sr.created_at
            FROM sales_returns sr JOIN invoices i ON i.id = sr.invoice_id
            WHERE sr.total_refund > 0;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'customer_payment', 'customer_payment', cp.id,
                   'customer-payment:' || cp.id, cp.user_id, cp.shift_id,
                   cp.customer_id, NULL, cp.tenant_id, cp.created_at
            FROM customer_payments cp;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'purchase_receipt', 'purchase_order', po.id,
                   'purchase-receipt:order:' || po.id, po.user_id, NULL, NULL,
                   po.supplier_id, po.tenant_id, po.created_at
            FROM purchase_orders po WHERE po.status = 'RECEIVED' AND po.total_cost > 0;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'purchase_return', 'purchase_return', pr.id,
                   'purchase-return:' || pr.id, pr.user_id, NULL, NULL,
                   pr.supplier_id, pr.tenant_id, pr.created_at
            FROM purchase_returns pr WHERE pr.total_refund > 0;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'supplier_payment', 'supplier_payment', sp.id,
                   'supplier-payment:' || sp.id, sp.user_id, sp.shift_id, NULL,
                   sp.supplier_id, sp.tenant_id, sp.payment_date
            FROM supplier_payments sp;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'expense', 'expense', e.id, 'expense:' || e.id, e.user_id, e.shift_id,
                   NULL, NULL, e.tenant_id, e.created_at FROM expenses e;

            INSERT INTO financial_transactions
                (event_type, source_type, source_id, source_key, actor_user_id, shift_id,
                 customer_id, supplier_id, tenant_id, occurred_at)
            SELECT 'shift_adjustment', 'shift', s.id, 'shift-adjustment:' || s.id,
                   s.user_id, s.id, NULL, NULL, s.tenant_id, s.end_time
            FROM shifts s WHERE s.status = 'CLOSED' AND s.difference <> 0;

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id,
                   CASE WHEN i.payment_type = 'cash' THEN 'cash_drawer' ELSE 'customer_receivable' END,
                   'debit', i.total_amount, ft.tenant_id
            FROM financial_transactions ft JOIN invoices i ON i.id = ft.source_id
            WHERE ft.event_type = 'sale';
            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, 'sales_revenue', 'credit', i.total_amount, ft.tenant_id
            FROM financial_transactions ft JOIN invoices i ON i.id = ft.source_id
            WHERE ft.event_type = 'sale';

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, 'sales_returns', 'debit', sr.total_refund, ft.tenant_id
            FROM financial_transactions ft JOIN sales_returns sr ON sr.id = ft.source_id
            WHERE ft.event_type = 'sales_return' AND sr.total_refund > 0;
            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, v.account_code, 'credit', v.amount, ft.tenant_id
            FROM financial_transactions ft JOIN sales_returns sr ON sr.id = ft.source_id
            CROSS JOIN LATERAL (VALUES
                ('cash_drawer', sr.cash_refund),
                ('customer_receivable', sr.debt_reduction),
                ('customer_credit', sr.credit_balance_increase)
            ) v(account_code, amount)
            WHERE ft.event_type = 'sales_return' AND v.amount > 0;

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, v.account_code, v.entry_side, cp.amount, ft.tenant_id
            FROM financial_transactions ft JOIN customer_payments cp ON cp.id = ft.source_id
            CROSS JOIN (VALUES ('cash_drawer', 'debit'), ('customer_receivable', 'credit'))
                v(account_code, entry_side)
            WHERE ft.event_type = 'customer_payment';

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, v.account_code, v.entry_side, po.total_cost, ft.tenant_id
            FROM financial_transactions ft JOIN purchase_orders po ON po.id = ft.source_id
            CROSS JOIN (VALUES ('inventory_purchases', 'debit'), ('supplier_payable', 'credit'))
                v(account_code, entry_side)
            WHERE ft.event_type = 'purchase_receipt';

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, v.account_code, v.entry_side, pr.total_refund, ft.tenant_id
            FROM financial_transactions ft JOIN purchase_returns pr ON pr.id = ft.source_id
            CROSS JOIN (VALUES ('supplier_payable', 'debit'), ('inventory_purchases', 'credit'))
                v(account_code, entry_side)
            WHERE ft.event_type = 'purchase_return';

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, 'supplier_payable', 'debit', sp.amount, ft.tenant_id
            FROM financial_transactions ft JOIN supplier_payments sp ON sp.id = ft.source_id
            WHERE ft.event_type = 'supplier_payment';
            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, CASE WHEN sp.payment_channel = 'cash' THEN 'cash_drawer' ELSE 'external_funds' END,
                   'credit', sp.amount, ft.tenant_id
            FROM financial_transactions ft JOIN supplier_payments sp ON sp.id = ft.source_id
            WHERE ft.event_type = 'supplier_payment';

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id, v.account_code, v.entry_side, e.amount, ft.tenant_id
            FROM financial_transactions ft JOIN expenses e ON e.id = ft.source_id
            CROSS JOIN (VALUES ('operating_expense', 'debit'), ('cash_drawer', 'credit'))
                v(account_code, entry_side)
            WHERE ft.event_type = 'expense';

            INSERT INTO financial_entries (transaction_id, account_code, entry_side, amount, tenant_id)
            SELECT ft.id,
                   CASE WHEN v.entry_side = 'debit' THEN
                       CASE WHEN s.difference > 0 THEN 'cash_drawer' ELSE 'cash_over_short' END
                   ELSE CASE WHEN s.difference > 0 THEN 'cash_over_short' ELSE 'cash_drawer' END END,
                   v.entry_side, abs(s.difference), ft.tenant_id
            FROM financial_transactions ft JOIN shifts s ON s.id = ft.source_id
            CROSS JOIN (VALUES ('debit'), ('credit')) v(entry_side)
            WHERE ft.event_type = 'shift_adjustment';
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_financial_entry_source ON financial_entries")
    op.execute("DROP TRIGGER IF EXISTS trg_financial_transaction_source ON financial_transactions")
    op.execute("DROP TRIGGER IF EXISTS trg_financial_entries_append_only ON financial_entries")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_financial_transactions_append_only ON financial_transactions"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_financial_transaction_source()")
    op.execute("DROP FUNCTION IF EXISTS reject_financial_ledger_mutation()")
    op.drop_table("financial_entries")
    op.drop_table("financial_transactions")
    op.drop_constraint(
        "fk_supplier_payments_shift_user_same_tenant", "supplier_payments", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_supplier_payments_channel_shift_context", "supplier_payments", type_="check"
    )
    op.drop_constraint("ck_supplier_payments_channel_canonical", "supplier_payments", type_="check")
    op.drop_column("supplier_payments", "shift_id")
    op.drop_column("supplier_payments", "payment_channel")
    op.drop_constraint("uq_expenses_id_tenant_id", "expenses", type_="unique")
    for table in ("expenses", "customer_payments"):
        op.drop_constraint(f"ck_{table}_request_fingerprint_length", table, type_="check")
        op.drop_constraint(f"ck_{table}_idempotency_key_length", table, type_="check")
        op.drop_constraint(f"uq_{table}_tenant_idempotency_key", table, type_="unique")
        op.drop_column(table, "request_fingerprint")
        op.drop_column(table, "idempotency_key")
