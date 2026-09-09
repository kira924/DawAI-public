import os
import subprocess
import sys

import pytest
from sqlalchemy import Numeric, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def _seed_resources(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) VALUES ('Sales Constraint A', 'sca@example.com', FALSE) RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) VALUES ('Sales Constraint B', 'scb@example.com', FALSE) RETURNING id"
            )
        )
        user_a = connection.scalar(
            text(
                "INSERT INTO users (email, hashed_password, full_name, role, tenant_id, is_active) VALUES ('sca@example.com', 'x', 'User A', 'pharmacist', :tenant, TRUE) RETURNING id"
            ),
            {"tenant": tenant_a},
        )
        user_b = connection.scalar(
            text(
                "INSERT INTO users (email, hashed_password, full_name, role, tenant_id, is_active) VALUES ('scb@example.com', 'x', 'User B', 'pharmacist', :tenant, TRUE) RETURNING id"
            ),
            {"tenant": tenant_b},
        )
        shift_a = connection.scalar(
            text(
                "INSERT INTO shifts (status, opening_balance, expected_closing_balance, user_id, tenant_id) VALUES ('OPEN', 0, 0, :user, :tenant) RETURNING id"
            ),
            {"user": user_a, "tenant": tenant_a},
        )
        shift_b = connection.scalar(
            text(
                "INSERT INTO shifts (status, opening_balance, expected_closing_balance, user_id, tenant_id) VALUES ('OPEN', 0, 0, :user, :tenant) RETURNING id"
            ),
            {"user": user_b, "tenant": tenant_b},
        )
        customer_a = connection.scalar(
            text(
                "INSERT INTO customers (name, credit_limit, total_debt, tenant_id) VALUES ('Customer A', 0, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant_a},
        )
        customer_b = connection.scalar(
            text(
                "INSERT INTO customers (name, credit_limit, total_debt, tenant_id) VALUES ('Customer B', 0, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant_b},
        )
        customer_a_secondary = connection.scalar(
            text(
                "INSERT INTO customers (name, credit_limit, total_debt, tenant_id) VALUES ('Customer A Secondary', 0, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant_a},
        )
        product_a = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) VALUES ('Product A', 'MEDICINE', 10, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant_a},
        )
        product_b = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) VALUES ('Product B', 'MEDICINE', 10, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant_b},
        )
        invoice_a = connection.scalar(
            text(
                "INSERT INTO invoices (total_amount, outstanding_amount, payment_type, customer_id, user_id, tenant_id, shift_id) VALUES (10, 0, 'cash', :customer, :user, :tenant, :shift) RETURNING id"
            ),
            {"customer": customer_a, "user": user_a, "tenant": tenant_a, "shift": shift_a},
        )
        invoice_b = connection.scalar(
            text(
                "INSERT INTO invoices (total_amount, outstanding_amount, payment_type, customer_id, user_id, tenant_id, shift_id) VALUES (10, 0, 'cash', :customer, :user, :tenant, :shift) RETURNING id"
            ),
            {"customer": customer_b, "user": user_b, "tenant": tenant_b, "shift": shift_b},
        )
        invoice_item_a = connection.scalar(
            text(
                "INSERT INTO invoice_items (invoice_id, product_id, tenant_id, quantity, list_unit_price, sale_unit_price, subtotal) VALUES (:invoice, :product, :tenant, 1, 10, 10, 10) RETURNING id"
            ),
            {"invoice": invoice_a, "product": product_a, "tenant": tenant_a},
        )
        invoice_item_b = connection.scalar(
            text(
                "INSERT INTO invoice_items (invoice_id, product_id, tenant_id, quantity, list_unit_price, sale_unit_price, subtotal) VALUES (:invoice, :product, :tenant, 1, 10, 10, 10) RETURNING id"
            ),
            {"invoice": invoice_b, "product": product_b, "tenant": tenant_b},
        )
        return_a = connection.scalar(
            text(
                "INSERT INTO sales_returns (idempotency_key, request_fingerprint, total_refund, cash_refund, debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, tenant_id) VALUES ('constraint-a', 'a', 10, 10, 0, 0, :invoice, :shift, :user, :tenant) RETURNING id"
            ),
            {"invoice": invoice_a, "shift": shift_a, "user": user_a, "tenant": tenant_a},
        )
        return_b = connection.scalar(
            text(
                "INSERT INTO sales_returns (idempotency_key, request_fingerprint, total_refund, cash_refund, debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, tenant_id) VALUES ('constraint-b', 'b', 10, 10, 0, 0, :invoice, :shift, :user, :tenant) RETURNING id"
            ),
            {"invoice": invoice_b, "shift": shift_b, "user": user_b, "tenant": tenant_b},
        )
        payment_a_secondary = connection.scalar(
            text(
                "INSERT INTO customer_payments (amount, customer_id, shift_id, user_id, tenant_id) VALUES (1, :customer, :shift, :user, :tenant) RETURNING id"
            ),
            {
                "customer": customer_a_secondary,
                "shift": shift_a,
                "user": user_a,
                "tenant": tenant_a,
            },
        )
    engine.dispose()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "shift_a": shift_a,
        "shift_b": shift_b,
        "customer_a": customer_a,
        "customer_b": customer_b,
        "customer_a_secondary": customer_a_secondary,
        "product_a": product_a,
        "product_b": product_b,
        "invoice_a": invoice_a,
        "invoice_b": invoice_b,
        "invoice_item_a": invoice_item_a,
        "invoice_item_b": invoice_item_b,
        "return_a": return_a,
        "return_b": return_b,
        "payment_a_secondary": payment_a_secondary,
    }


@pytest.mark.integration
def test_sales_schema_contains_named_tenant_constraints(
    migrated_postgresql_database_url: str,
) -> None:
    inspector = inspect(create_engine(migrated_postgresql_database_url))
    expected_unique = {
        "customers": {"uq_customers_id_tenant_id"},
        "customer_payments": {
            "uq_customer_payments_id_tenant_id",
            "uq_customer_payments_id_customer_tenant_id",
        },
        "customer_payment_allocations": {"uq_customer_payment_allocations_payment_invoice"},
        "shifts": {"uq_shifts_id_user_id_tenant_id"},
        "invoices": {
            "uq_invoices_id_tenant_id",
            "uq_invoices_id_customer_tenant_id",
        },
        "invoice_items": {"uq_invoice_items_identity_context"},
        "invoice_item_batch_allocations": {"uq_invoice_batch_allocations_item_batch"},
        "sales_returns": {
            "uq_sales_returns_id_tenant_id",
            "uq_sales_returns_tenant_idempotency_key",
        },
        "quarantine_items": {"uq_quarantine_items_return_item"},
    }
    expected_fks = {
        "invoices": {
            "fk_invoices_customer_same_tenant",
            "fk_invoices_user_same_tenant",
            "fk_invoices_shift_user_same_tenant",
        },
        "invoice_items": {
            "fk_invoice_items_invoice_same_tenant",
            "fk_invoice_items_product_same_tenant",
        },
        "invoice_item_batch_allocations": {
            "fk_invoice_batch_allocations_item_context",
            "fk_invoice_batch_allocations_batch_context",
        },
        "customer_payment_allocations": {
            "fk_customer_payment_allocations_payment_context",
            "fk_customer_payment_allocations_invoice_context",
        },
        "sales_returns": {
            "fk_sales_returns_invoice_same_tenant",
            "fk_sales_returns_shift_user_same_tenant",
            "fk_sales_returns_user_same_tenant",
        },
        "sales_return_items": {
            "fk_sales_return_items_return_context",
            "fk_sales_return_items_invoice_item_context",
            "fk_sales_return_items_batch_context",
        },
        "quarantine_items": {
            "fk_quarantine_items_return_item_context",
            "fk_quarantine_items_batch_context",
        },
    }
    expected_checks = {
        "products": {
            "ck_products_price_nonnegative",
            "ck_products_part_price_nonnegative",
        },
        "expenses": {"ck_expenses_amount_positive"},
        "shifts": {
            "ck_shifts_status_canonical",
            "ck_shifts_opening_balance_nonnegative",
            "ck_shifts_expected_balance_nonnegative",
            "ck_shifts_actual_balance_nonnegative",
        },
        "purchase_orders": {
            "ck_purchase_orders_subtotal_nonnegative",
            "ck_purchase_orders_discount_nonnegative",
            "ck_purchase_orders_tax_percentage_range",
            "ck_purchase_orders_tax_value_nonnegative",
            "ck_purchase_orders_total_cost_nonnegative",
            "ck_purchase_orders_status_canonical",
            "ck_purchase_orders_discount_not_above_subtotal",
            "ck_purchase_orders_tax_matches_net_subtotal",
            "ck_purchase_orders_tax_free_mvp",
            "ck_purchase_orders_total_matches_components",
        },
        "purchase_items": {
            "ck_purchase_items_quantity_positive",
            "ck_purchase_items_base_cost_nonnegative",
            "ck_purchase_items_discount_percentage_range",
            "ck_purchase_items_discount_value_nonnegative",
            "ck_purchase_items_unit_cost_nonnegative",
            "ck_purchase_items_subtotal_nonnegative",
            "ck_purchase_items_unit_cost_matches_discount",
            "ck_purchase_items_subtotal_matches_unit_cost",
            "ck_purchase_items_discount_matches_subtotal",
        },
        "supplier_payments": {"ck_supplier_payments_amount_positive"},
        "purchase_returns": {"ck_purchase_returns_total_refund_nonnegative"},
        "purchase_return_items": {
            "ck_purchase_return_items_quantity_positive",
            "ck_purchase_return_items_unit_refund_nonnegative",
            "ck_purchase_return_items_subtotal_nonnegative",
            "ck_purchase_return_items_subtotal_matches_unit_refund",
        },
    }
    for table, names in expected_unique.items():
        assert names.issubset({item["name"] for item in inspector.get_unique_constraints(table)})
    for table, names in expected_fks.items():
        assert names.issubset({item["name"] for item in inspector.get_foreign_keys(table)})
    for table, names in expected_checks.items():
        assert names.issubset({item["name"] for item in inspector.get_check_constraints(table)})
    for table in ("invoice_items", "sales_return_items"):
        tenant_column = next(
            column for column in inspector.get_columns(table) if column["name"] == "tenant_id"
        )
        assert tenant_column["nullable"] is False


@pytest.mark.integration
def test_financial_columns_use_approved_exact_numeric_types(
    migrated_postgresql_database_url: str,
) -> None:
    inspector = inspect(create_engine(migrated_postgresql_database_url))
    money_columns = {
        "customers": {"credit_limit", "total_debt", "credit_balance"},
        "customer_payments": {"amount"},
        "shifts": {
            "opening_balance",
            "expected_closing_balance",
            "actual_closing_balance",
            "difference",
        },
        "products": {"price", "part_price"},
        "invoices": {"total_amount", "outstanding_amount"},
        "invoice_items": {
            "list_unit_price",
            "sale_unit_price",
            "subtotal",
        },
        "sales_returns": {
            "total_refund",
            "cash_refund",
            "debt_reduction",
            "credit_balance_increase",
        },
        "sales_return_items": {"unit_refund", "refund_amount"},
        "expenses": {"amount"},
        "purchase_orders": {
            "subtotal",
            "total_discount",
            "total_cost",
            "tax_value",
        },
        "purchase_items": {
            "base_cost",
            "discount_value",
            "unit_cost",
            "subtotal",
        },
        "supplier_payments": {"amount"},
        "purchase_returns": {"total_refund"},
        "purchase_return_items": {"unit_refund", "subtotal"},
    }
    for table_name, expected_names in money_columns.items():
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        for column_name in expected_names:
            column_type = columns[column_name]["type"]
            assert isinstance(column_type, Numeric)
            assert column_type.precision == 18
            assert column_type.scale == 2

    for table_name, column_name in (
        ("purchase_orders", "tax_percentage"),
        ("purchase_items", "discount_percentage"),
    ):
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        column_type = columns[column_name]["type"]
        assert isinstance(column_type, Numeric)
        assert column_type.precision == 7
        assert column_type.scale == 4


@pytest.mark.integration
def test_database_rejects_cross_tenant_sales_relationships(
    migrated_postgresql_database_url: str,
) -> None:
    ids = _seed_resources(migrated_postgresql_database_url)
    unsafe = [
        (
            "INSERT INTO invoices (total_amount, payment_type, customer_id, user_id, tenant_id, shift_id) VALUES (10, 'cash', :customer_b, :user_a, :tenant_a, :shift_a)",
            ids,
        ),
        (
            "INSERT INTO invoices (total_amount, payment_type, user_id, tenant_id) VALUES (10, 'cash', :user_b, :tenant_a)",
            ids,
        ),
        (
            "INSERT INTO invoices (total_amount, payment_type, user_id, tenant_id, shift_id) VALUES (10, 'cash', :user_a, :tenant_a, :shift_b)",
            ids,
        ),
        (
            "INSERT INTO invoice_items (invoice_id, product_id, tenant_id, quantity, list_unit_price, sale_unit_price, subtotal) VALUES (:invoice_a, :product_b, :tenant_a, 1, 10, 10, 10)",
            ids,
        ),
        (
            "INSERT INTO invoice_items (invoice_id, product_id, tenant_id, quantity, list_unit_price, sale_unit_price, subtotal) VALUES (:invoice_b, :product_a, :tenant_a, 1, 10, 10, 10)",
            ids,
        ),
        (
            "INSERT INTO sales_returns (idempotency_key, request_fingerprint, total_refund, cash_refund, debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, tenant_id) VALUES ('unsafe-1', 'x', 10, 10, 0, 0, :invoice_b, :shift_a, :user_a, :tenant_a)",
            ids,
        ),
        (
            "INSERT INTO sales_returns (idempotency_key, request_fingerprint, total_refund, cash_refund, debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, tenant_id) VALUES ('unsafe-2', 'x', 10, 10, 0, 0, :invoice_a, :shift_b, :user_a, :tenant_a)",
            ids,
        ),
        (
            "INSERT INTO sales_returns (idempotency_key, request_fingerprint, total_refund, cash_refund, debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, tenant_id) VALUES ('unsafe-3', 'x', 10, 10, 0, 0, :invoice_a, :shift_a, :user_b, :tenant_a)",
            ids,
        ),
        (
            "INSERT INTO sales_return_items (sales_return_id, invoice_id, invoice_item_id, product_id, tenant_id, quantity, condition, disposition, unit_refund, refund_amount) VALUES (:return_a, :invoice_a, :invoice_item_b, :product_b, :tenant_a, 1, 'legacy', 'legacy', 10, 10)",
            ids,
        ),
        (
            "INSERT INTO sales_return_items (sales_return_id, invoice_id, invoice_item_id, product_id, tenant_id, quantity, condition, disposition, unit_refund, refund_amount) VALUES (:return_b, :invoice_b, :invoice_item_a, :product_a, :tenant_a, 1, 'legacy', 'legacy', 10, 10)",
            ids,
        ),
        (
            "INSERT INTO customer_payment_allocations (payment_id, invoice_id, customer_id, amount, tenant_id) VALUES (:payment_a_secondary, :invoice_a, :customer_a_secondary, 1, :tenant_a)",
            ids,
        ),
        (
            "INSERT INTO invoices (total_amount, outstanding_amount, payment_type, customer_id, user_id, tenant_id, shift_id) VALUES (10, 1, 'cash', :customer_a, :user_a, :tenant_a, :shift_a)",
            ids,
        ),
        (
            "INSERT INTO invoices (total_amount, outstanding_amount, payment_type, user_id, tenant_id, shift_id) VALUES (10, 10, 'credit', :user_a, :tenant_a, :shift_a)",
            ids,
        ),
        (
            "INSERT INTO invoice_items (invoice_id, product_id, tenant_id, quantity, list_unit_price, sale_unit_price, subtotal) VALUES (:invoice_a, :product_a, :tenant_a, 1, 10, 10, 9)",
            ids,
        ),
        (
            "INSERT INTO sales_returns (idempotency_key, request_fingerprint, total_refund, cash_refund, debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, tenant_id) VALUES ('unsafe-breakdown', 'x', 10, 9, 0, 0, :invoice_a, :shift_a, :user_a, :tenant_a)",
            ids,
        ),
        (
            "INSERT INTO sales_return_items (sales_return_id, invoice_id, invoice_item_id, product_id, tenant_id, quantity, condition, disposition, unit_refund, refund_amount) VALUES (:return_a, :invoice_a, :invoice_item_a, :product_a, :tenant_a, 1, 'legacy', 'legacy', 10, 9)",
            ids,
        ),
    ]
    engine = create_engine(migrated_postgresql_database_url)
    for statement, parameters in unsafe:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement), parameters)
    engine.dispose()


@pytest.mark.integration
def test_sales_tenant_migration_fails_closed_on_unsafe_legacy_rows(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "8c4f1d2e6a90")
    assert baseline.returncode == 0, baseline.stderr
    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) VALUES ('Unsafe A', 'unsafe-a@example.com', FALSE) RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) VALUES ('Unsafe B', 'unsafe-b@example.com', FALSE) RETURNING id"
            )
        )
        user_a = connection.scalar(
            text(
                "INSERT INTO users (email, hashed_password, full_name, role, tenant_id, is_active) VALUES ('unsafe-user@example.com', 'x', 'Unsafe User', 'pharmacist', :tenant, TRUE) RETURNING id"
            ),
            {"tenant": tenant_a},
        )
        customer_b = connection.scalar(
            text(
                "INSERT INTO customers (name, credit_limit, total_debt, tenant_id) VALUES ('Foreign Customer', 0, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant_b},
        )
        connection.execute(
            text(
                "INSERT INTO invoices (total_amount, payment_type, customer_id, user_id, tenant_id) VALUES (10, 'cash', :customer, :user, :tenant)"
            ),
            {"customer": customer_b, "user": user_a, "tenant": tenant_a},
        )
    engine.dispose()
    result = _run_alembic(database_url, "upgrade", "head")
    assert result.returncode != 0
    assert "Unsafe sales tenant relationships detected" in result.stderr
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "8c4f1d2e6a90"
        assert "tenant_id" not in {
            column["name"] for column in inspect(connection).get_columns("invoice_items")
        }
    engine.dispose()


@pytest.mark.integration
def test_return_integrity_migration_rejects_unsafe_money_and_payment_history(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "2d91b7e4c6f0")
    assert baseline.returncode == 0, baseline.stderr
    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) VALUES ('Unsafe Money', 'unsafe-money@example.com', FALSE) RETURNING id"
            )
        )
        user = connection.scalar(
            text(
                "INSERT INTO users (email, hashed_password, full_name, role, tenant_id, is_active) VALUES ('unsafe-money@example.com', 'x', 'Unsafe Money User', 'pharmacist', :tenant, TRUE) RETURNING id"
            ),
            {"tenant": tenant},
        )
        shift = connection.scalar(
            text(
                "INSERT INTO shifts (status, opening_balance, expected_closing_balance, user_id, tenant_id) VALUES ('OPEN', 0, 0, :user, :tenant) RETURNING id"
            ),
            {"user": user, "tenant": tenant},
        )
        customer = connection.scalar(
            text(
                "INSERT INTO customers (name, credit_limit, total_debt, tenant_id) VALUES ('Unsafe Money Customer', 100, 1, :tenant) RETURNING id"
            ),
            {"tenant": tenant},
        )
        connection.execute(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) VALUES ('Unsafe Precision', 'MEDICINE', -10.001, 0, :tenant)"
            ),
            {"tenant": tenant},
        )
        connection.execute(
            text(
                "INSERT INTO customer_payments (amount, customer_id, shift_id, user_id, tenant_id) VALUES (1, :customer, :shift, :user, :tenant)"
            ),
            {
                "customer": customer,
                "shift": shift,
                "user": user,
                "tenant": tenant,
            },
        )
    engine.dispose()

    result = _run_alembic(database_url, "upgrade", "head")
    assert result.returncode != 0
    assert "Unsafe sales return integrity backfill" in result.stderr
    assert "customer payments=1" in result.stderr
    assert "ck_products_price_nonnegative=1" in result.stderr
    assert "products.price=1" in result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "2d91b7e4c6f0"
        assert "return_policy" not in {
            column["name"] for column in inspect(connection).get_columns("products")
        }
    engine.dispose()


@pytest.mark.integration
def test_sales_tenant_migration_backfills_items_and_round_trips(
    empty_postgresql_database_url: str,
) -> None:
    url = empty_postgresql_database_url
    assert _run_alembic(url, "upgrade", "8c4f1d2e6a90").returncode == 0
    engine = create_engine(url)
    with engine.begin() as connection:
        tenant = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) VALUES ('Legacy Sales', 'legacy@example.com', FALSE) RETURNING id"
            )
        )
        user = connection.scalar(
            text(
                "INSERT INTO users (email, hashed_password, full_name, role, tenant_id, is_active) VALUES ('legacy@example.com', 'x', 'Legacy User', 'pharmacist', :tenant, TRUE) RETURNING id"
            ),
            {"tenant": tenant},
        )
        product = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) VALUES ('Legacy Product', 'MEDICINE', 10, 0, :tenant) RETURNING id"
            ),
            {"tenant": tenant},
        )
        invoice = connection.scalar(
            text(
                "INSERT INTO invoices (total_amount, payment_type, user_id, tenant_id, shift_id) VALUES (10, 'cash', :user, :tenant, NULL) RETURNING id"
            ),
            {"user": user, "tenant": tenant},
        )
        item = connection.scalar(
            text(
                "INSERT INTO invoice_items (invoice_id, product_id, quantity, unit_price, subtotal) VALUES (:invoice, :product, 1, 10, 10) RETURNING id"
            ),
            {"invoice": invoice, "product": product},
        )
        shift = connection.scalar(
            text(
                "INSERT INTO shifts "
                "(status, start_time, opening_balance, expected_closing_balance, "
                "user_id, tenant_id) "
                "VALUES ('OPEN', now(), 0, 0, :user, :tenant) RETURNING id"
            ),
            {"user": user, "tenant": tenant},
        )
        sales_return = connection.scalar(
            text(
                "INSERT INTO sales_returns "
                "(total_refund, invoice_id, shift_id, user_id, tenant_id) "
                "VALUES (10, :invoice, :shift, :user, :tenant) RETURNING id"
            ),
            {"invoice": invoice, "shift": shift, "user": user, "tenant": tenant},
        )
        return_item = connection.scalar(
            text(
                "INSERT INTO sales_return_items "
                "(sales_return_id, product_id, quantity, refund_amount) "
                "VALUES (:sales_return, :product, 1, 10) RETURNING id"
            ),
            {"sales_return": sales_return, "product": product},
        )
    engine.dispose()
    upgrade = _run_alembic(url, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    engine = create_engine(url)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT tenant_id FROM invoice_items WHERE id=:id"), {"id": item}
            )
            == tenant
        )
        assert (
            connection.scalar(
                text("SELECT tenant_id FROM sales_return_items WHERE id=:id"),
                {"id": return_item},
            )
            == tenant
        )
        assert (
            connection.scalar(text("SELECT shift_id FROM invoices WHERE id=:id"), {"id": invoice})
            is None
        )
    engine.dispose()
    assert _run_alembic(url, "downgrade", "8c4f1d2e6a90").returncode == 0
    assert _run_alembic(url, "upgrade", "head").returncode == 0
    drift = _run_alembic(url, "check")
    assert drift.returncode == 0, drift.stderr
