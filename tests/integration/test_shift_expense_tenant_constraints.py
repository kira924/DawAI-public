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


def _seed_two_tenants(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Shift Tenant A', 'shift-a@example.test', FALSE) RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Shift Tenant B', 'shift-b@example.test', FALSE) RETURNING id"
            )
        )
        user_a = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('shift-user-a@example.test', 'synthetic-hash', "
                "'Shift User A', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        user_a_peer = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('shift-user-a-peer@example.test', 'synthetic-hash', "
                "'Shift User A Peer', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        user_b = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('shift-user-b@example.test', 'synthetic-hash', "
                "'Shift User B', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )
        shift_a = connection.scalar(
            text(
                "INSERT INTO shifts "
                "(status, opening_balance, expected_closing_balance, user_id, tenant_id) "
                "VALUES ('OPEN', 0, 0, :user_id, :tenant_id) RETURNING id"
            ),
            {"user_id": user_a, "tenant_id": tenant_a},
        )
        shift_b = connection.scalar(
            text(
                "INSERT INTO shifts "
                "(status, opening_balance, expected_closing_balance, user_id, tenant_id) "
                "VALUES ('OPEN', 0, 0, :user_id, :tenant_id) RETURNING id"
            ),
            {"user_id": user_b, "tenant_id": tenant_b},
        )
    engine.dispose()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_a_peer": user_a_peer,
        "user_b": user_b,
        "shift_a": shift_a,
        "shift_b": shift_b,
    }


@pytest.mark.integration
def test_database_rejects_cross_tenant_shift_and_expense_relationships(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_two_tenants(migrated_postgresql_database_url)
    engine = create_engine(migrated_postgresql_database_url)

    unsafe_statements = [
        (
            "INSERT INTO shifts "
            "(status, opening_balance, expected_closing_balance, user_id, tenant_id) "
            "VALUES ('OPEN', 0, 0, :user_id, :tenant_id)",
            {"user_id": identifiers["user_b"], "tenant_id": identifiers["tenant_a"]},
        ),
        (
            "INSERT INTO expenses (amount, description, shift_id, user_id, tenant_id) "
            "VALUES (10, 'Cross-tenant shift', :shift_id, :user_id, :tenant_id)",
            {
                "shift_id": identifiers["shift_b"],
                "user_id": identifiers["user_b"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
        (
            "INSERT INTO expenses (amount, description, shift_id, user_id, tenant_id) "
            "VALUES (10, 'Wrong same-tenant user', :shift_id, :user_id, :tenant_id)",
            {
                "shift_id": identifiers["shift_a"],
                "user_id": identifiers["user_a_peer"],
                "tenant_id": identifiers["tenant_a"],
            },
        ),
    ]

    for statement, parameters in unsafe_statements:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement), parameters)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO expenses (amount, description, shift_id, user_id, tenant_id) "
                "VALUES (10, 'Valid expense', :shift_id, :user_id, :tenant_id)"
            ),
            {
                "shift_id": identifiers["shift_a"],
                "user_id": identifiers["user_a"],
                "tenant_id": identifiers["tenant_a"],
            },
        )

    engine.dispose()


@pytest.mark.integration
def test_shift_expense_constraints_are_present(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with engine.connect() as connection:
        foreign_keys = {
            foreign_key["name"]
            for table_name in ("shifts", "expenses")
            for foreign_key in inspect(connection).get_foreign_keys(table_name)
        }

    assert {
        "fk_shifts_user_same_tenant",
        "fk_expenses_shift_user_same_tenant",
        "fk_expenses_user_same_tenant",
    }.issubset(foreign_keys)
    engine.dispose()


@pytest.mark.integration
def test_shift_expense_migration_fails_closed_on_unsafe_legacy_rows(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "6b4d8f2a9c31")
    assert baseline.returncode == 0, baseline.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Unsafe Shift Tenant A', 'unsafe-shift-a@example.test', FALSE) "
                "RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Unsafe Shift Tenant B', 'unsafe-shift-b@example.test', FALSE) "
                "RETURNING id"
            )
        )
        user_b = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('unsafe-shift-user@example.test', 'synthetic-hash', "
                "'Unsafe Shift User', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )
        connection.execute(
            text(
                "INSERT INTO shifts "
                "(status, opening_balance, expected_closing_balance, user_id, tenant_id) "
                "VALUES ('OPEN', 0, 0, :user_id, :tenant_id)"
            ),
            {"user_id": user_b, "tenant_id": tenant_a},
        )
    engine.dispose()

    result = _run_alembic(database_url, "upgrade", "head")

    assert result.returncode != 0
    assert "Unsafe shift or expense tenant relationships detected" in result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "6b4d8f2a9c31"
    engine.dispose()


@pytest.mark.integration
def test_shift_expense_migration_round_trip(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url

    upgrade = _run_alembic(database_url, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    downgrade = _run_alembic(database_url, "downgrade", "6b4d8f2a9c31")
    assert downgrade.returncode == 0, downgrade.stderr

    second_upgrade = _run_alembic(database_url, "upgrade", "head")
    assert second_upgrade.returncode == 0, second_upgrade.stderr

    drift = _run_alembic(database_url, "check")
    assert drift.returncode == 0, drift.stderr
