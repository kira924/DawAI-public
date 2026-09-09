import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
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


def _seed_constraint_resources(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Purchase Constraint Tenant A', "
                "'purchase-constraint-a@example.com', FALSE) RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Purchase Constraint Tenant B', "
                "'purchase-constraint-b@example.com', FALSE) RETURNING id"
            )
        )
        user_a = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('purchase-user-a@example.com', 'synthetic-hash', "
                "'Purchase User A', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        user_b = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('purchase-user-b@example.com', 'synthetic-hash', "
                "'Purchase User B', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )
        supplier_a = connection.scalar(
            text(
                "INSERT INTO suppliers (name, tenant_id) "
                "VALUES ('Purchase Supplier A', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        supplier_b = connection.scalar(
            text(
                "INSERT INTO suppliers (name, tenant_id) "
                "VALUES ('Purchase Supplier B', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )
        product_a = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) "
                "VALUES ('Purchase Product A', 'MEDICINE', 10, 1, :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        product_b = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) "
                "VALUES ('Purchase Product B', 'MEDICINE', 10, 1, :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )
        order_a = connection.scalar(
            text(
                "INSERT INTO purchase_orders "
                "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
                "supplier_id, user_id, tenant_id) "
                "VALUES ('RECEIVED', 10, 0, 0, 0, 10, :supplier_id, :user_id, :tenant_id) "
                "RETURNING id"
            ),
            {"supplier_id": supplier_a, "user_id": user_a, "tenant_id": tenant_a},
        )
        order_b = connection.scalar(
            text(
                "INSERT INTO purchase_orders "
                "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
                "supplier_id, user_id, tenant_id) "
                "VALUES ('RECEIVED', 10, 0, 0, 0, 10, :supplier_id, :user_id, :tenant_id) "
                "RETURNING id"
            ),
            {"supplier_id": supplier_b, "user_id": user_b, "tenant_id": tenant_b},
        )
        batch_a = connection.scalar(
            text(
                "INSERT INTO product_batches "
                "(batch_number, expiry_date, quantity, product_id, tenant_id) "
                "VALUES ('CONSTRAINT-A', '2030-01-01', 1, :product_id, :tenant_id) RETURNING id"
            ),
            {"product_id": product_a, "tenant_id": tenant_a},
        )
        batch_b = connection.scalar(
            text(
                "INSERT INTO product_batches "
                "(batch_number, expiry_date, quantity, product_id, tenant_id) "
                "VALUES ('CONSTRAINT-B', '2030-01-01', 1, :product_id, :tenant_id) RETURNING id"
            ),
            {"product_id": product_b, "tenant_id": tenant_b},
        )
        item_a = connection.scalar(
            text(
                "INSERT INTO purchase_items "
                "(purchase_id, product_id, received_batch_id, tenant_id, quantity, base_cost, "
                "discount_percentage, discount_value, unit_cost, subtotal, batch_number, expiry_date) "
                "VALUES (:order_id, :product_id, :batch_id, :tenant_id, 1, 10, 0, 0, 10, 10, "
                "'CONSTRAINT-A', '2030-01-01') RETURNING id"
            ),
            {
                "order_id": order_a,
                "product_id": product_a,
                "batch_id": batch_a,
                "tenant_id": tenant_a,
            },
        )
        item_b = connection.scalar(
            text(
                "INSERT INTO purchase_items "
                "(purchase_id, product_id, received_batch_id, tenant_id, quantity, base_cost, "
                "discount_percentage, discount_value, unit_cost, subtotal, batch_number, expiry_date) "
                "VALUES (:order_id, :product_id, :batch_id, :tenant_id, 1, 10, 0, 0, 10, 10, "
                "'CONSTRAINT-B', '2030-01-01') RETURNING id"
            ),
            {
                "order_id": order_b,
                "product_id": product_b,
                "batch_id": batch_b,
                "tenant_id": tenant_b,
            },
        )
        purchase_return_a = connection.scalar(
            text(
                "INSERT INTO purchase_returns "
                "(idempotency_key, request_fingerprint, total_refund, purchase_order_id, "
                "supplier_id, user_id, tenant_id) "
                "VALUES ('constraint-return-a', :fingerprint, 10, :order_id, :supplier_id, "
                ":user_id, :tenant_id) RETURNING id"
            ),
            {
                "fingerprint": "a" * 64,
                "order_id": order_a,
                "supplier_id": supplier_a,
                "user_id": user_a,
                "tenant_id": tenant_a,
            },
        )
        purchase_return_b = connection.scalar(
            text(
                "INSERT INTO purchase_returns "
                "(idempotency_key, request_fingerprint, total_refund, purchase_order_id, "
                "supplier_id, user_id, tenant_id) "
                "VALUES ('constraint-return-b', :fingerprint, 10, :order_id, :supplier_id, "
                ":user_id, :tenant_id) RETURNING id"
            ),
            {
                "fingerprint": "b" * 64,
                "order_id": order_b,
                "supplier_id": supplier_b,
                "user_id": user_b,
                "tenant_id": tenant_b,
            },
        )

    engine.dispose()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "supplier_a": supplier_a,
        "supplier_b": supplier_b,
        "product_a": product_a,
        "product_b": product_b,
        "order_a": order_a,
        "order_b": order_b,
        "purchase_return_a": purchase_return_a,
        "purchase_return_b": purchase_return_b,
        "batch_a": batch_a,
        "batch_b": batch_b,
        "item_a": item_a,
        "item_b": item_b,
    }


@pytest.mark.integration
def test_purchase_schema_contains_named_tenant_constraints(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    schema_inspector = inspect(engine)

    expected_unique_constraints = {
        "suppliers": {"uq_suppliers_id_tenant_id"},
        "products": {"uq_products_id_tenant_id"},
        "purchase_orders": {
            "uq_purchase_orders_id_tenant_id",
            "uq_purchase_orders_id_supplier_tenant_id",
        },
        "product_batches": {
            "uq_product_batches_product_batch_tenant_id",
        },
        "purchase_items": {"uq_purchase_items_return_context"},
        "supplier_payments": {"uq_supplier_payments_tenant_idempotency_key"},
        "purchase_returns": {
            "uq_purchase_returns_id_tenant_id",
            "uq_purchase_returns_id_order_tenant_id",
            "uq_purchase_returns_tenant_idempotency_key",
        },
    }
    for table_name, expected_names in expected_unique_constraints.items():
        actual_names = {
            constraint["name"] for constraint in schema_inspector.get_unique_constraints(table_name)
        }
        assert expected_names.issubset(actual_names)

    expected_foreign_keys = {
        "purchase_orders": {
            "fk_purchase_orders_supplier_same_tenant",
            "fk_purchase_orders_user_same_tenant",
        },
        "purchase_items": {
            "fk_purchase_items_order_same_tenant",
            "fk_purchase_items_product_same_tenant",
            "fk_purchase_items_received_batch_context",
        },
        "supplier_payments": {
            "fk_supplier_payments_supplier_same_tenant",
            "fk_supplier_payments_user_same_tenant",
        },
        "purchase_returns": {
            "fk_purchase_returns_order_supplier_same_tenant",
            "fk_purchase_returns_supplier_same_tenant",
            "fk_purchase_returns_user_same_tenant",
        },
        "purchase_return_items": {
            "fk_purchase_return_items_return_context",
            "fk_purchase_return_items_purchase_item_context",
            "fk_purchase_return_items_batch_context",
        },
        "product_batches": {"fk_product_batches_product_same_tenant"},
    }
    for table_name, expected_names in expected_foreign_keys.items():
        actual_names = {
            foreign_key["name"] for foreign_key in schema_inspector.get_foreign_keys(table_name)
        }
        assert expected_names.issubset(actual_names)

    for table_name in ("purchase_items", "purchase_return_items"):
        tenant_column = next(
            column
            for column in schema_inspector.get_columns(table_name)
            if column["name"] == "tenant_id"
        )
        assert tenant_column["nullable"] is False

    engine.dispose()


@pytest.mark.integration
def test_database_rejects_cross_tenant_purchase_relationships(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_constraint_resources(migrated_postgresql_database_url)
    engine = create_engine(migrated_postgresql_database_url)

    unsafe_statements = [
        (
            "INSERT INTO purchase_orders "
            "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
            "supplier_id, user_id, tenant_id) "
            "VALUES ('PENDING', 10, 0, 0, 0, 10, :supplier_id, :user_id, :tenant_id)",
            {
                "supplier_id": identifiers["supplier_b"],
                "user_id": identifiers["user_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_orders "
            "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
            "supplier_id, user_id, tenant_id) "
            "VALUES ('PENDING', 10, 0, 0, 0, 10, :supplier_id, :user_id, :tenant_id)",
            {
                "supplier_id": identifiers["supplier_a"],
                "user_id": identifiers["user_b"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_items "
            "(purchase_id, product_id, tenant_id, quantity, base_cost, unit_cost, subtotal) "
            "VALUES (:order_id, :product_id, :tenant_id, 1, 10, 10, 10)",
            {
                "order_id": identifiers["order_a"],
                "product_id": identifiers["product_b"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_items "
            "(purchase_id, product_id, tenant_id, quantity, base_cost, unit_cost, subtotal) "
            "VALUES (:order_id, :product_id, :tenant_id, 1, 10, 10, 10)",
            {
                "order_id": identifiers["order_b"],
                "product_id": identifiers["product_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO supplier_payments "
            "(idempotency_key, request_fingerprint, amount, supplier_id, user_id, tenant_id) "
            "VALUES ('unsafe-payment-a', :fingerprint, 10, :supplier_id, :user_id, :tenant_id)",
            {
                "fingerprint": "c" * 64,
                "supplier_id": identifiers["supplier_b"],
                "user_id": identifiers["user_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO supplier_payments "
            "(idempotency_key, request_fingerprint, amount, supplier_id, user_id, tenant_id) "
            "VALUES ('unsafe-payment-b', :fingerprint, 10, :supplier_id, :user_id, :tenant_id)",
            {
                "fingerprint": "d" * 64,
                "supplier_id": identifiers["supplier_a"],
                "user_id": identifiers["user_b"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_returns "
            "(idempotency_key, request_fingerprint, total_refund, purchase_order_id, "
            "supplier_id, user_id, tenant_id) "
            "VALUES ('unsafe-return-a', :fingerprint, 10, :order_id, :supplier_id, "
            ":user_id, :tenant_id)",
            {
                "fingerprint": "e" * 64,
                "order_id": identifiers["order_b"],
                "supplier_id": identifiers["supplier_a"],
                "user_id": identifiers["user_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_returns "
            "(idempotency_key, request_fingerprint, total_refund, purchase_order_id, "
            "supplier_id, user_id, tenant_id) "
            "VALUES ('unsafe-return-b', :fingerprint, 10, :order_id, :supplier_id, "
            ":user_id, :tenant_id)",
            {
                "fingerprint": "f" * 64,
                "order_id": identifiers["order_a"],
                "supplier_id": identifiers["supplier_b"],
                "user_id": identifiers["user_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_returns "
            "(idempotency_key, request_fingerprint, total_refund, purchase_order_id, "
            "supplier_id, user_id, tenant_id) "
            "VALUES ('unsafe-return-c', :fingerprint, 10, :order_id, :supplier_id, "
            ":user_id, :tenant_id)",
            {
                "fingerprint": "g" * 64,
                "order_id": identifiers["order_a"],
                "supplier_id": identifiers["supplier_a"],
                "user_id": identifiers["user_b"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_return_items "
            "(return_id, purchase_order_id, purchase_item_id, batch_id, product_id, "
            "tenant_id, quantity, unit_refund, subtotal) "
            "VALUES (:return_id, :order_id, :item_id, :batch_id, :product_id, "
            ":tenant_id, 1, 10, 10)",
            {
                "return_id": identifiers["purchase_return_a"],
                "order_id": identifiers["order_a"],
                "item_id": identifiers["item_b"],
                "batch_id": identifiers["batch_b"],
                "product_id": identifiers["product_b"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO purchase_return_items "
            "(return_id, purchase_order_id, purchase_item_id, batch_id, product_id, "
            "tenant_id, quantity, unit_refund, subtotal) "
            "VALUES (:return_id, :order_id, :item_id, :batch_id, :product_id, "
            ":tenant_id, 1, 10, 10)",
            {
                "return_id": identifiers["purchase_return_b"],
                "order_id": identifiers["order_b"],
                "item_id": identifiers["item_a"],
                "batch_id": identifiers["batch_a"],
                "product_id": identifiers["product_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
    ]
    for statement, parameters in unsafe_statements:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement), parameters)

    engine.dispose()


@pytest.mark.integration
def test_purchase_tenant_migration_fails_closed_on_unsafe_legacy_rows(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "3f2a9c7d1e84")
    assert baseline.returncode == 0, baseline.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Unsafe Purchase Tenant A', 'unsafe-a@example.com', FALSE) RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Unsafe Purchase Tenant B', 'unsafe-b@example.com', FALSE) RETURNING id"
            )
        )
        user_a = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('unsafe-user-a@example.com', 'synthetic-hash', "
                "'Unsafe User A', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        supplier_b = connection.scalar(
            text(
                "INSERT INTO suppliers (name, tenant_id) "
                "VALUES ('Unsafe Supplier B', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )
        connection.execute(
            text(
                "INSERT INTO purchase_orders "
                "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
                "supplier_id, user_id, tenant_id) "
                "VALUES ('PENDING', 10, 0, 0, 0, 10, :supplier_id, :user_id, :tenant_id)"
            ),
            {"supplier_id": supplier_b, "user_id": user_a, "tenant_id": tenant_a},
        )
    engine.dispose()

    result = _run_alembic(database_url, "upgrade", "head")

    assert result.returncode != 0
    assert "Unsafe purchase tenant relationships detected" in result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "3f2a9c7d1e84"
        assert "tenant_id" not in {
            column["name"] for column in inspect(connection).get_columns("purchase_items")
        }
    engine.dispose()


@pytest.mark.integration
def test_purchase_tenant_migration_round_trip(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url

    upgrade = _run_alembic(database_url, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    downgrade = _run_alembic(database_url, "downgrade", "3f2a9c7d1e84")
    assert downgrade.returncode == 0, downgrade.stderr

    second_upgrade = _run_alembic(database_url, "upgrade", "head")
    assert second_upgrade.returncode == 0, second_upgrade.stderr

    drift = _run_alembic(database_url, "check")
    assert drift.returncode == 0, drift.stderr


@pytest.mark.integration
def test_purchase_integrity_migration_fails_closed_on_unmapped_received_item(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "7a3e5c9d1f20")
    assert baseline.returncode == 0, baseline.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Unsafe Integrity Tenant', 'unsafe-integrity@example.com', FALSE) "
                "RETURNING id"
            )
        )
        user_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('unsafe-integrity-user@example.com', 'synthetic-hash', "
                "'Unsafe Integrity User', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        supplier_id = connection.scalar(
            text(
                "INSERT INTO suppliers (name, tenant_id) "
                "VALUES ('Unsafe Integrity Supplier', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        product_id = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) "
                "VALUES ('Unsafe Integrity Product', 'MEDICINE', 10, 1, :tenant_id) "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        order_id = connection.scalar(
            text(
                "INSERT INTO purchase_orders "
                "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
                "supplier_id, user_id, tenant_id) "
                "VALUES ('RECEIVED', 10, 0, 0, 0, 10, :supplier_id, :user_id, :tenant_id) "
                "RETURNING id"
            ),
            {"supplier_id": supplier_id, "user_id": user_id, "tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO purchase_items "
                "(purchase_id, product_id, tenant_id, quantity, base_cost, discount_percentage, "
                "discount_value, unit_cost, subtotal, batch_number, expiry_date) "
                "VALUES (:order_id, :product_id, :tenant_id, 1, 10, 0, 0, 10, 10, "
                "'MISSING-BATCH', '2030-01-01')"
            ),
            {"order_id": order_id, "product_id": product_id, "tenant_id": tenant_id},
        )
    engine.dispose()

    result = _run_alembic(database_url, "upgrade", "head")
    assert result.returncode != 0
    assert "Unsafe purchase integrity history detected" in result.stderr
    assert "unmapped received purchase items=1" in result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "7a3e5c9d1f20"
        assert "received_batch_id" not in {
            column["name"] for column in inspect(connection).get_columns("purchase_items")
        }
    engine.dispose()


@pytest.mark.integration
def test_purchase_integrity_migration_reconciles_only_approved_dummy_signature(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "7a3e5c9d1f20")
    assert baseline.returncode == 0, baseline.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Approved Dummy Tenant', 'approved-dummy@example.test', FALSE) "
                "RETURNING id"
            )
        )
        user_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('approved-dummy-user@example.test', 'synthetic-hash', "
                "'Approved Dummy User', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        supplier_id = connection.scalar(
            text(
                "INSERT INTO suppliers (name, tenant_id) "
                "VALUES ('Approved Dummy Supplier', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        product_id = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) "
                "VALUES ('Approved Dummy Product', 'MEDICINE', 10, 25, :tenant_id) "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        batch_definitions = [
            ("APPROVED-BATCH-1", "2030-01-01", 10),
            ("APPROVED-BATCH-2", "2031-01-01", 10),
            ("APPROVED-BATCH-3", "2032-01-01", 5),
        ]
        for batch_number, expiry_date, quantity in batch_definitions:
            connection.execute(
                text(
                    "INSERT INTO product_batches "
                    "(batch_number, expiry_date, quantity, product_id, tenant_id) "
                    "VALUES (:batch_number, :expiry_date, :quantity, :product_id, :tenant_id)"
                ),
                {
                    "batch_number": batch_number,
                    "expiry_date": expiry_date,
                    "quantity": quantity,
                    "product_id": product_id,
                    "tenant_id": tenant_id,
                },
            )

        order_ids = []
        for index in range(5):
            has_tax = index < 3
            quantity = 10 if index == 0 else 1
            subtotal = 100 if index == 0 else 10
            total_discount = 20 if index == 0 else 2
            tax_value = 8 if has_tax and index == 0 else (0.8 if has_tax else 0)
            total_cost = subtotal - total_discount + tax_value
            order_id = connection.scalar(
                text(
                    "INSERT INTO purchase_orders "
                    "(status, subtotal, total_discount, tax_percentage, tax_value, total_cost, "
                    "supplier_id, user_id, tenant_id) "
                    "VALUES ('RECEIVED', :subtotal, :total_discount, :tax_percentage, "
                    ":tax_value, :total_cost, :supplier_id, :user_id, :tenant_id) RETURNING id"
                ),
                {
                    "subtotal": subtotal,
                    "total_discount": total_discount,
                    "tax_percentage": 10 if has_tax else 0,
                    "tax_value": tax_value,
                    "total_cost": total_cost,
                    "supplier_id": supplier_id,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                },
            )
            order_ids.append(order_id)
            if index == 0:
                batch_number = "UNKNOWN-DUMMY-BATCH"
                expiry_date = "2040-01-01"
            else:
                batch_number, expiry_date, _ = batch_definitions[(index - 1) % 3]
            connection.execute(
                text(
                    "INSERT INTO purchase_items "
                    "(purchase_id, product_id, tenant_id, quantity, base_cost, "
                    "discount_percentage, discount_value, unit_cost, subtotal, "
                    "batch_number, expiry_date) "
                    "VALUES (:purchase_id, :product_id, :tenant_id, :quantity, 10, "
                    "20, :discount_value, 8, :item_subtotal, :batch_number, :expiry_date)"
                ),
                {
                    "purchase_id": order_id,
                    "product_id": product_id,
                    "tenant_id": tenant_id,
                    "quantity": quantity,
                    "discount_value": total_discount,
                    "item_subtotal": subtotal - total_discount,
                    "batch_number": batch_number,
                    "expiry_date": expiry_date,
                },
            )

        return_id = connection.scalar(
            text(
                "INSERT INTO purchase_returns "
                "(total_refund, purchase_order_id, supplier_id, user_id, tenant_id) "
                "VALUES (10, NULL, :supplier_id, :user_id, :tenant_id) RETURNING id"
            ),
            {"supplier_id": supplier_id, "user_id": user_id, "tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO purchase_return_items "
                "(return_id, product_id, tenant_id, quantity, unit_refund, subtotal) "
                "VALUES (:return_id, :product_id, :tenant_id, 1, 10, 10)"
            ),
            {"return_id": return_id, "product_id": product_id, "tenant_id": tenant_id},
        )
    engine.dispose()

    result = _run_alembic(
        database_url,
        "-x",
        "allow_approved_dummy_purchase_reconciliation=true",
        "upgrade",
        "head",
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "8e2f6a1c9d40"
        assert connection.scalar(text("SELECT COUNT(*) FROM purchase_returns")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM purchase_return_items")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM purchase_orders")) == 4
        assert connection.scalar(text("SELECT COUNT(*) FROM purchase_items")) == 4
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM purchase_items "
                    "WHERE received_batch_id IS NULL "
                    "OR unit_cost <> round(base_cost * (100 - discount_percentage) / 100, 2) "
                    "OR subtotal <> unit_cost * quantity "
                    "OR discount_value <> base_cost * quantity - subtotal"
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM purchase_orders "
                    "WHERE tax_percentage <> 0 OR tax_value <> 0 "
                    "OR total_cost <> subtotal - total_discount"
                )
            )
            == 0
        )
        assert order_ids[0] not in set(
            connection.scalars(text("SELECT id FROM purchase_orders")).all()
        )
    engine.dispose()
