"""Enforce purchase return and supplier financial integrity.

Revision ID: 6b4d8f2a9c31
Revises: 7a3e5c9d1f20
Create Date: 2026-08-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "6b4d8f2a9c31"
down_revision: str | Sequence[str] | None = "7a3e5c9d1f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _approved_dummy_reconciliation_requested() -> bool:
    value = context.get_x_argument(as_dictionary=True).get(
        "allow_approved_dummy_purchase_reconciliation"
    )
    return value == "true"


def _reconcile_approved_dummy_history() -> None:
    if not _approved_dummy_reconciliation_requested():
        return

    connection = op.get_bind()
    signature_query = sa.text(
        """
        WITH unmapped_items AS (
            SELECT purchase_item.id,
                   purchase_item.purchase_id,
                   purchase_item.product_id,
                   purchase_item.tenant_id,
                   purchase_item.quantity,
                   purchase_item.expiry_date
            FROM purchase_items purchase_item
            JOIN purchase_orders purchase_order ON purchase_order.id=purchase_item.purchase_id
            WHERE purchase_order.status='RECEIVED'
              AND (
                purchase_item.batch_number IS NULL
                OR purchase_item.expiry_date IS NULL
                OR (
                    SELECT COUNT(*) FROM product_batches exact_batch
                    WHERE exact_batch.product_id=purchase_item.product_id
                      AND exact_batch.tenant_id=purchase_item.tenant_id
                      AND exact_batch.batch_number=purchase_item.batch_number
                      AND exact_batch.expiry_date=purchase_item.expiry_date
                ) <> 1
              )
        ), unmapped_shape AS (
            SELECT unmapped_item.id,
                   COUNT(DISTINCT sibling.id) AS order_item_count,
                   COUNT(DISTINCT candidate.id) AS candidate_batch_count,
                   COUNT(DISTINCT candidate.id) FILTER (
                       WHERE candidate.quantity >= unmapped_item.quantity
                   ) AS sufficient_batch_count,
                   COUNT(DISTINCT candidate.id) FILTER (
                       WHERE candidate.expiry_date=unmapped_item.expiry_date
                   ) AS same_expiry_batch_count
            FROM unmapped_items unmapped_item
            JOIN purchase_items sibling
              ON sibling.purchase_id=unmapped_item.purchase_id
             AND sibling.tenant_id=unmapped_item.tenant_id
            LEFT JOIN product_batches candidate
              ON candidate.product_id=unmapped_item.product_id
             AND candidate.tenant_id=unmapped_item.tenant_id
            GROUP BY unmapped_item.id
        ), orphan_order_candidates AS (
            SELECT purchase_return.id,
                   COUNT(DISTINCT purchase_order.id) AS candidate_order_count
            FROM purchase_returns purchase_return
            JOIN purchase_return_items return_item
              ON return_item.return_id=purchase_return.id
            LEFT JOIN purchase_orders purchase_order
              ON purchase_order.supplier_id=purchase_return.supplier_id
             AND purchase_order.tenant_id=purchase_return.tenant_id
             AND purchase_order.status='RECEIVED'
            LEFT JOIN purchase_items purchase_item
              ON purchase_item.purchase_id=purchase_order.id
             AND purchase_item.product_id=return_item.product_id
             AND purchase_item.tenant_id=return_item.tenant_id
            WHERE purchase_return.purchase_order_id IS NULL
              AND purchase_item.id IS NOT NULL
            GROUP BY purchase_return.id
        )
        SELECT
            (SELECT COUNT(*) FROM purchase_returns
             WHERE purchase_order_id IS NULL) AS orphan_returns,
            (SELECT COUNT(*)
             FROM purchase_return_items return_item
             JOIN purchase_returns purchase_return ON purchase_return.id=return_item.return_id
             WHERE purchase_return.purchase_order_id IS NULL) AS orphan_return_items,
            (SELECT COUNT(*) FROM unmapped_items) AS unmapped_received_items,
            (SELECT COUNT(DISTINCT purchase_id) FROM unmapped_items) AS unmapped_orders,
            (SELECT COUNT(*) FROM unmapped_shape
             WHERE order_item_count=1) AS single_item_orders,
            (SELECT COALESCE(SUM(candidate_batch_count), 0)
             FROM unmapped_shape) AS candidate_batches,
            (SELECT COALESCE(SUM(sufficient_batch_count), 0)
             FROM unmapped_shape) AS sufficient_batches,
            (SELECT COALESCE(SUM(same_expiry_batch_count), 0)
             FROM unmapped_shape) AS same_expiry_batches,
            (SELECT COALESCE(SUM(candidate_order_count), 0)
             FROM orphan_order_candidates) AS orphan_candidate_orders,
            (SELECT COUNT(*) FROM purchase_items
             WHERE quantity <= 0 OR base_cost < 0
                OR discount_percentage < 0 OR discount_percentage > 100) AS invalid_source_items,
            (SELECT COUNT(*)
             FROM products product
             WHERE product.total_parts <> (
                 SELECT COALESCE(SUM(product_batch.quantity), 0)
                 FROM product_batches product_batch
                 WHERE product_batch.product_id=product.id
                   AND product_batch.tenant_id=product.tenant_id
             )) AS inventory_mismatches
        """
    )
    signature = connection.execute(signature_query).one()._mapping
    expected_signature = {
        "orphan_returns": 1,
        "orphan_return_items": 1,
        "unmapped_received_items": 1,
        "unmapped_orders": 1,
        "single_item_orders": 1,
        "candidate_batches": 3,
        "sufficient_batches": 2,
        "same_expiry_batches": 0,
        "orphan_candidate_orders": 5,
        "invalid_source_items": 0,
        "inventory_mismatches": 0,
    }
    actual_signature = {name: int(signature[name] or 0) for name in expected_signature}
    if actual_signature != expected_signature:
        raise RuntimeError(
            "Approved dummy purchase reconciliation signature mismatch: "
            f"expected {expected_signature}, got {actual_signature}"
        )

    op.execute(
        """
        CREATE TEMPORARY TABLE dawai_approved_dummy_purchase_orders
        ON COMMIT DROP AS
        SELECT DISTINCT purchase_item.purchase_id
        FROM purchase_items purchase_item
        JOIN purchase_orders purchase_order ON purchase_order.id=purchase_item.purchase_id
        WHERE purchase_order.status='RECEIVED'
          AND (
            purchase_item.batch_number IS NULL
            OR purchase_item.expiry_date IS NULL
            OR (
                SELECT COUNT(*) FROM product_batches exact_batch
                WHERE exact_batch.product_id=purchase_item.product_id
                  AND exact_batch.tenant_id=purchase_item.tenant_id
                  AND exact_batch.batch_number=purchase_item.batch_number
                  AND exact_batch.expiry_date=purchase_item.expiry_date
            ) <> 1
          )
        """
    )
    op.execute(
        """
        DELETE FROM purchase_return_items return_item
        USING purchase_returns purchase_return
        WHERE purchase_return.id=return_item.return_id
          AND purchase_return.purchase_order_id IS NULL
        """
    )
    op.execute("DELETE FROM purchase_returns WHERE purchase_order_id IS NULL")
    op.execute(
        """
        DELETE FROM purchase_items purchase_item
        USING dawai_approved_dummy_purchase_orders approved_order
        WHERE approved_order.purchase_id=purchase_item.purchase_id
        """
    )
    op.execute(
        """
        DELETE FROM purchase_orders purchase_order
        USING dawai_approved_dummy_purchase_orders approved_order
        WHERE approved_order.purchase_id=purchase_order.id
        """
    )
    op.execute(
        """
        UPDATE purchase_items
        SET unit_cost=round(base_cost * (100 - discount_percentage) / 100, 2),
            subtotal=round(base_cost * (100 - discount_percentage) / 100, 2) * quantity,
            discount_value=base_cost * quantity
                - round(base_cost * (100 - discount_percentage) / 100, 2) * quantity
        """
    )
    op.execute(
        """
        WITH item_totals AS (
            SELECT purchase_order.id,
                   COALESCE(SUM(purchase_item.base_cost * purchase_item.quantity), 0) AS subtotal,
                   COALESCE(SUM(purchase_item.discount_value), 0) AS total_discount
            FROM purchase_orders purchase_order
            LEFT JOIN purchase_items purchase_item
              ON purchase_item.purchase_id=purchase_order.id
            GROUP BY purchase_order.id
        )
        UPDATE purchase_orders purchase_order
        SET subtotal=item_totals.subtotal,
            total_discount=item_totals.total_discount,
            tax_percentage=0,
            tax_value=0,
            total_cost=item_totals.subtotal - item_totals.total_discount
        FROM item_totals
        WHERE item_totals.id=purchase_order.id
        """
    )


def _assert_safe_backfill() -> None:
    checks = {
        "duplicate product batches": """
            SELECT COUNT(*) FROM (
                SELECT product_id, batch_number, tenant_id
                FROM product_batches
                GROUP BY product_id, batch_number, tenant_id
                HAVING COUNT(*) > 1
            ) duplicate_batch
        """,
        "cross-tenant product batches": """
            SELECT COUNT(*)
            FROM product_batches batch
            JOIN products product ON product.id=batch.product_id
            WHERE batch.tenant_id IS DISTINCT FROM product.tenant_id
        """,
        "unmapped received purchase items": """
            SELECT COUNT(*)
            FROM purchase_items item
            JOIN purchase_orders purchase_order ON purchase_order.id=item.purchase_id
            WHERE purchase_order.status='RECEIVED'
              AND (
                item.batch_number IS NULL
                OR item.expiry_date IS NULL
                OR (
                    SELECT COUNT(*) FROM product_batches batch
                    WHERE batch.product_id=item.product_id
                      AND batch.tenant_id=item.tenant_id
                      AND batch.batch_number=item.batch_number
                      AND batch.expiry_date=item.expiry_date
                ) <> 1
              )
        """,
        "returns without purchase orders": """
            SELECT COUNT(*) FROM purchase_returns WHERE purchase_order_id IS NULL
        """,
        "ambiguous legacy purchase return items": """
            SELECT COUNT(*)
            FROM purchase_return_items return_item
            JOIN purchase_returns purchase_return ON purchase_return.id=return_item.return_id
            WHERE (
                SELECT COUNT(*) FROM purchase_items purchase_item
                WHERE purchase_item.purchase_id=purchase_return.purchase_order_id
                  AND purchase_item.product_id=return_item.product_id
                  AND purchase_item.tenant_id=return_item.tenant_id
            ) <> 1
        """,
        "legacy return items without received batches": """
            SELECT COUNT(*)
            FROM purchase_return_items return_item
            JOIN purchase_returns purchase_return ON purchase_return.id=return_item.return_id
            JOIN purchase_items purchase_item
              ON purchase_item.purchase_id=purchase_return.purchase_order_id
             AND purchase_item.product_id=return_item.product_id
             AND purchase_item.tenant_id=return_item.tenant_id
            JOIN purchase_orders purchase_order ON purchase_order.id=purchase_item.purchase_id
            WHERE purchase_order.status <> 'RECEIVED'
               OR (
                    SELECT COUNT(*) FROM product_batches batch
                    WHERE batch.product_id=purchase_item.product_id
                      AND batch.tenant_id=purchase_item.tenant_id
                      AND batch.batch_number=purchase_item.batch_number
                      AND batch.expiry_date=purchase_item.expiry_date
               ) <> 1
        """,
        "cumulative purchase return excess": """
            SELECT COUNT(*) FROM (
                SELECT purchase_item.id
                FROM purchase_items purchase_item
                JOIN purchase_returns purchase_return
                  ON purchase_return.purchase_order_id=purchase_item.purchase_id
                 AND purchase_return.tenant_id=purchase_item.tenant_id
                JOIN purchase_return_items return_item
                  ON return_item.return_id=purchase_return.id
                 AND return_item.product_id=purchase_item.product_id
                 AND return_item.tenant_id=purchase_item.tenant_id
                GROUP BY purchase_item.id, purchase_item.quantity
                HAVING SUM(return_item.quantity) > purchase_item.quantity
            ) excess
        """,
        "invalid purchase order status": """
            SELECT COUNT(*) FROM purchase_orders
            WHERE status NOT IN ('PENDING', 'RECEIVED', 'CANCELLED')
        """,
        "nonzero purchase tax in tax-free MVP": """
            SELECT COUNT(*) FROM purchase_orders
            WHERE tax_percentage <> 0 OR tax_value <> 0
        """,
        "purchase item arithmetic mismatch": """
            SELECT COUNT(*) FROM purchase_items
            WHERE unit_cost <> round(base_cost * (100 - discount_percentage) / 100, 2)
               OR subtotal <> unit_cost * quantity
               OR discount_value <> base_cost * quantity - subtotal
        """,
        "purchase order arithmetic mismatch": """
            SELECT COUNT(*) FROM purchase_orders
            WHERE subtotal < total_discount
               OR tax_value <> round((subtotal - total_discount) * tax_percentage / 100, 2)
               OR total_cost <> subtotal - total_discount + tax_value
        """,
        "purchase order item total mismatch": """
            SELECT COUNT(*)
            FROM purchase_orders purchase_order
            LEFT JOIN (
                SELECT purchase_id,
                       SUM(base_cost * quantity) AS subtotal,
                       SUM(discount_value) AS total_discount
                FROM purchase_items
                GROUP BY purchase_id
            ) item_totals ON item_totals.purchase_id=purchase_order.id
            WHERE purchase_order.subtotal <> COALESCE(item_totals.subtotal, 0)
               OR purchase_order.total_discount <> COALESCE(item_totals.total_discount, 0)
        """,
        "purchase return item arithmetic mismatch": """
            SELECT COUNT(*) FROM purchase_return_items
            WHERE subtotal <> unit_refund * quantity
        """,
        "purchase return total mismatch": """
            SELECT COUNT(*)
            FROM purchase_returns purchase_return
            LEFT JOIN (
                SELECT return_id, SUM(subtotal) AS total
                FROM purchase_return_items
                GROUP BY return_id
            ) item_totals ON item_totals.return_id=purchase_return.id
            WHERE purchase_return.total_refund <> COALESCE(item_totals.total, 0)
        """,
    }
    connection = op.get_bind()
    failures = {name: int(connection.scalar(sa.text(query)) or 0) for name, query in checks.items()}
    unsafe = {name: count for name, count in failures.items() if count}
    if unsafe:
        summary = ", ".join(f"{name}={count}" for name, count in sorted(unsafe.items()))
        raise RuntimeError(f"Unsafe purchase integrity history detected: {summary}")


def _drop_foreign_key(table_name: str, constrained_columns: Sequence[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    matches = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key["constrained_columns"] == list(constrained_columns)
    ]
    if len(matches) != 1 or not matches[0]["name"]:
        columns = ", ".join(constrained_columns)
        raise RuntimeError(
            f"Expected one named foreign key on {table_name}({columns}), found {len(matches)}"
        )
    op.drop_constraint(matches[0]["name"], table_name, type_="foreignkey")


def upgrade() -> None:
    """Fail closed on ambiguous history and add purchase accounting invariants."""
    _reconcile_approved_dummy_history()
    _assert_safe_backfill()

    op.add_column("purchase_items", sa.Column("received_batch_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE purchase_items purchase_item
        SET received_batch_id=batch.id
        FROM purchase_orders purchase_order, product_batches batch
        WHERE purchase_order.id=purchase_item.purchase_id
          AND purchase_order.status='RECEIVED'
          AND batch.product_id=purchase_item.product_id
          AND batch.tenant_id=purchase_item.tenant_id
          AND batch.batch_number=purchase_item.batch_number
          AND batch.expiry_date=purchase_item.expiry_date
        """
    )

    op.add_column(
        "supplier_payments",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "supplier_payments",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE supplier_payments
        SET idempotency_key='legacy-supplier-payment-' || id,
            request_fingerprint=md5('legacy-supplier-payment-' || id)
        """
    )
    op.alter_column("supplier_payments", "idempotency_key", nullable=False)
    op.alter_column("supplier_payments", "request_fingerprint", nullable=False)

    op.add_column(
        "purchase_returns",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "purchase_returns",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE purchase_returns
        SET idempotency_key='legacy-purchase-return-' || id,
            request_fingerprint=md5('legacy-purchase-return-' || id)
        """
    )
    op.alter_column("purchase_returns", "idempotency_key", nullable=False)
    op.alter_column("purchase_returns", "request_fingerprint", nullable=False)
    op.alter_column("purchase_returns", "purchase_order_id", nullable=False)

    for column_name in ("purchase_order_id", "purchase_item_id", "batch_id"):
        op.add_column(
            "purchase_return_items",
            sa.Column(column_name, sa.Integer(), nullable=True),
        )
    op.execute(
        """
        UPDATE purchase_return_items return_item
        SET purchase_order_id=purchase_return.purchase_order_id,
            purchase_item_id=purchase_item.id,
            batch_id=batch.id
        FROM purchase_returns purchase_return,
             purchase_items purchase_item,
             product_batches batch
        WHERE purchase_return.id=return_item.return_id
          AND purchase_item.purchase_id=purchase_return.purchase_order_id
          AND purchase_item.product_id=return_item.product_id
          AND purchase_item.tenant_id=return_item.tenant_id
          AND batch.product_id=purchase_item.product_id
          AND batch.tenant_id=purchase_item.tenant_id
          AND batch.batch_number=purchase_item.batch_number
          AND batch.expiry_date=purchase_item.expiry_date
        """
    )
    for column_name in ("purchase_order_id", "purchase_item_id", "batch_id"):
        op.alter_column("purchase_return_items", column_name, nullable=False)

    op.create_check_constraint(
        "ck_purchase_orders_status_canonical",
        "purchase_orders",
        "status IN ('PENDING', 'RECEIVED', 'CANCELLED')",
    )
    op.create_check_constraint(
        "ck_purchase_orders_discount_not_above_subtotal",
        "purchase_orders",
        "subtotal >= total_discount",
    )
    op.create_check_constraint(
        "ck_purchase_orders_tax_matches_net_subtotal",
        "purchase_orders",
        "tax_value = round((subtotal - total_discount) * tax_percentage / 100, 2)",
    )
    op.create_check_constraint(
        "ck_purchase_orders_total_matches_components",
        "purchase_orders",
        "total_cost = subtotal - total_discount + tax_value",
    )
    op.create_check_constraint(
        "ck_purchase_orders_tax_free_mvp",
        "purchase_orders",
        "tax_percentage = 0 AND tax_value = 0",
    )
    op.create_check_constraint(
        "ck_purchase_items_unit_cost_matches_discount",
        "purchase_items",
        "unit_cost = round(base_cost * (100 - discount_percentage) / 100, 2)",
    )
    op.create_check_constraint(
        "ck_purchase_items_subtotal_matches_unit_cost",
        "purchase_items",
        "subtotal = unit_cost * quantity",
    )
    op.create_check_constraint(
        "ck_purchase_items_discount_matches_subtotal",
        "purchase_items",
        "discount_value = base_cost * quantity - subtotal",
    )
    op.create_check_constraint(
        "ck_purchase_return_items_subtotal_matches_unit_refund",
        "purchase_return_items",
        "subtotal = unit_refund * quantity",
    )

    op.create_unique_constraint(
        "uq_product_batches_product_batch_tenant_id",
        "product_batches",
        ["product_id", "batch_number", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_purchase_items_return_context",
        "purchase_items",
        ["id", "purchase_id", "product_id", "received_batch_id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_supplier_payments_tenant_idempotency_key",
        "supplier_payments",
        ["tenant_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_purchase_returns_id_order_tenant_id",
        "purchase_returns",
        ["id", "purchase_order_id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_purchase_returns_tenant_idempotency_key",
        "purchase_returns",
        ["tenant_id", "idempotency_key"],
    )

    _drop_foreign_key("product_batches", ["product_id"])
    op.create_foreign_key(
        "fk_product_batches_product_same_tenant",
        "product_batches",
        "products",
        ["product_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_items_received_batch_context",
        "purchase_items",
        "product_batches",
        ["received_batch_id", "product_id", "tenant_id"],
        ["id", "product_id", "tenant_id"],
    )

    op.drop_constraint(
        "fk_purchase_return_items_return_same_tenant",
        "purchase_return_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_purchase_return_items_product_same_tenant",
        "purchase_return_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_purchase_return_items_return_context",
        "purchase_return_items",
        "purchase_returns",
        ["return_id", "purchase_order_id", "tenant_id"],
        ["id", "purchase_order_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_return_items_purchase_item_context",
        "purchase_return_items",
        "purchase_items",
        ["purchase_item_id", "purchase_order_id", "product_id", "batch_id", "tenant_id"],
        ["id", "purchase_id", "product_id", "received_batch_id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_purchase_return_items_batch_context",
        "purchase_return_items",
        "product_batches",
        ["batch_id", "product_id", "tenant_id"],
        ["id", "product_id", "tenant_id"],
    )


def downgrade() -> None:
    """Restore the pre-integrity purchase schema."""
    for constraint_name in (
        "fk_purchase_return_items_batch_context",
        "fk_purchase_return_items_purchase_item_context",
        "fk_purchase_return_items_return_context",
    ):
        op.drop_constraint(constraint_name, "purchase_return_items", type_="foreignkey")
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

    op.drop_constraint(
        "fk_purchase_items_received_batch_context",
        "purchase_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_product_batches_product_same_tenant",
        "product_batches",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "product_batches_product_id_fkey",
        "product_batches",
        "products",
        ["product_id"],
        ["id"],
    )

    for table_name, constraint_name in (
        ("purchase_returns", "uq_purchase_returns_tenant_idempotency_key"),
        ("purchase_returns", "uq_purchase_returns_id_order_tenant_id"),
        ("supplier_payments", "uq_supplier_payments_tenant_idempotency_key"),
        ("purchase_items", "uq_purchase_items_return_context"),
        ("product_batches", "uq_product_batches_product_batch_tenant_id"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="unique")

    for table_name, constraint_name in (
        ("purchase_return_items", "ck_purchase_return_items_subtotal_matches_unit_refund"),
        ("purchase_items", "ck_purchase_items_discount_matches_subtotal"),
        ("purchase_items", "ck_purchase_items_subtotal_matches_unit_cost"),
        ("purchase_items", "ck_purchase_items_unit_cost_matches_discount"),
        ("purchase_orders", "ck_purchase_orders_total_matches_components"),
        ("purchase_orders", "ck_purchase_orders_tax_free_mvp"),
        ("purchase_orders", "ck_purchase_orders_tax_matches_net_subtotal"),
        ("purchase_orders", "ck_purchase_orders_discount_not_above_subtotal"),
        ("purchase_orders", "ck_purchase_orders_status_canonical"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")

    for column_name in ("batch_id", "purchase_item_id", "purchase_order_id"):
        op.drop_column("purchase_return_items", column_name)
    op.alter_column("purchase_returns", "purchase_order_id", nullable=True)
    for column_name in ("request_fingerprint", "idempotency_key"):
        op.drop_column("purchase_returns", column_name)
        op.drop_column("supplier_payments", column_name)
    op.drop_column("purchase_items", "received_batch_id")
