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


@pytest.mark.integration
def test_empty_postgresql_database_upgrades_to_head(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    engine = create_engine(database_url)
    try:
        existing_tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not existing_tables, "Migration test database must be empty"

    result = _run_alembic(database_url, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    drift_result = _run_alembic(database_url, "check")
    assert drift_result.returncode == 0, drift_result.stderr


@pytest.mark.integration
def test_approved_dummy_data_is_reconciled_and_constrained(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    result = _run_alembic(database_url, "upgrade", "b8f9c2d14a6e")
    assert result.returncode == 0, result.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email) "
                "VALUES ('Synthetic Pharmacy', 'owner@example.test') RETURNING id"
            )
        )
        ownerless_tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Ownerless Synthetic Pharmacy', "
                "'ownerless@example.test', TRUE) RETURNING id"
            )
        )
        invalid_user_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id) "
                "VALUES ('invalid@example.test', 'synthetic-hash', 'Invalid Role', "
                "'string', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        admin_user_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id) "
                "VALUES ('admin@example.test', 'synthetic-hash', 'Legacy Admin', "
                "'admin', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        product_id = connection.scalar(
            text(
                "INSERT INTO products "
                "(name, category, price, total_parts, tenant_id) "
                "VALUES ('Synthetic Product', 'MEDICINE', 10, 5, :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO product_batches "
                "(batch_number, expiry_date, quantity, product_id, tenant_id) "
                "VALUES ('SYNTHETIC-BATCH', DATE '2030-01-01', 4, :product_id, :tenant_id)"
            ),
            {"product_id": product_id, "tenant_id": tenant_id},
        )
        invoice_id = connection.scalar(
            text(
                "INSERT INTO invoices (total_amount, payment_type, user_id, tenant_id) "
                "VALUES (10, NULL, :user_id, :tenant_id) RETURNING id"
            ),
            {"user_id": invalid_user_id, "tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO invoice_items (invoice_id, product_id, quantity, unit_price, subtotal) "
                "VALUES (:invoice_id, :product_id, 1, 10, 10)"
            ),
            {"invoice_id": invoice_id, "product_id": product_id},
        )

    engine.dispose()
    result = _run_alembic(database_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT total_parts FROM products WHERE id = :id"), {"id": product_id}
            )
            == 4
        )
        assert (
            connection.scalar(
                text("SELECT payment_type FROM invoices WHERE id = :id"), {"id": invoice_id}
            )
            == "cash"
        )
        assert (
            connection.scalar(
                text("SELECT role FROM users WHERE id = :id"), {"id": invalid_user_id}
            )
            == "manager"
        )
        assert (
            connection.scalar(text("SELECT role FROM users WHERE id = :id"), {"id": admin_user_id})
            == "super_admin"
        )
        owner_record = connection.execute(
            text("SELECT owner_user_id, owner_email, is_active FROM tenants WHERE id = :id"),
            {"id": tenant_id},
        ).one()
        assert owner_record == (invalid_user_id, "invalid@example.test", False)
        ownerless_record = connection.execute(
            text("SELECT owner_user_id, is_active FROM tenants WHERE id = :id"),
            {"id": ownerless_tenant_id},
        ).one()
        assert ownerless_record == (None, False)

        expected_constraints = {
            "ck_invoices_payment_type_canonical",
            "ck_product_batches_quantity_nonnegative",
            "ck_products_total_parts_nonnegative",
            "ck_users_role_canonical",
        }
        actual_constraints = {
            constraint["name"]
            for table_name in ("invoices", "product_batches", "products", "users")
            for constraint in inspect(connection).get_check_constraints(table_name)
        }
        assert expected_constraints.issubset(actual_constraints)

        tenant_constraints = {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("tenants")
        }
        assert "ck_tenants_active_requires_owner" in tenant_constraints
        user_unique_constraints = {
            constraint["name"] for constraint in inspect(connection).get_unique_constraints("users")
        }
        assert "uq_users_id_tenant_id" in user_unique_constraints
        assert "tenant_ownership_transfers" in inspect(connection).get_table_names()

    engine.dispose()


@pytest.mark.integration
def test_database_rejects_missing_and_cross_tenant_owners(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Invalid Active Tenant', 'invalid-active@example.test', TRUE)"
            )
        )

    with engine.begin() as connection:
        tenant_a_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Constraint Tenant A', 'constraint-a@example.test', FALSE) "
                "RETURNING id"
            )
        )
        tenant_b_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Constraint Tenant B', 'constraint-b@example.test', FALSE) "
                "RETURNING id"
            )
        )
        manager_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('constraint-manager@example.test', 'synthetic-hash', "
                "'Constraint Manager', 'manager', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_a_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE tenants SET owner_user_id = :owner_id WHERE id = :tenant_id"),
            {"owner_id": manager_id, "tenant_id": tenant_b_id},
        )

    engine.dispose()
