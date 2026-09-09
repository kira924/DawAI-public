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
def test_auth_schema_and_same_tenant_constraints_are_enforced(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with engine.begin() as connection:
        tenant_a = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Auth Constraint A', 'auth-constraint-a@example.com', FALSE) RETURNING id"
            )
        )
        tenant_b = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Auth Constraint B', 'auth-constraint-b@example.com', FALSE) RETURNING id"
            )
        )
        user_a = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('auth-constraint-user-a@example.com', 'synthetic-hash', "
                "'Auth User A', 'manager', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_a},
        )
        user_b = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('auth-constraint-user-b@example.com', 'synthetic-hash', "
                "'Auth User B', 'super_admin', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_b},
        )

    unsafe_statements = [
        (
            "INSERT INTO auth_sessions "
            "(id, family_id, refresh_token_hash, user_id, tenant_id, created_at, expires_at) "
            "VALUES ('00000000-0000-0000-0000-000000000001', "
            "'00000000-0000-0000-0000-000000000002', :token_hash, :user_id, :tenant_id, "
            "now(), now() + interval '1 day')",
            {"token_hash": "a" * 64, "user_id": user_b, "tenant_id": tenant_a},
        ),
        (
            "INSERT INTO password_reset_tokens "
            "(id, token_hash, user_id, tenant_id, requested_by_user_id, created_at, expires_at) "
            "VALUES ('00000000-0000-0000-0000-000000000003', :token_hash, :user_id, "
            ":tenant_id, :requester_id, now(), now() + interval '30 minutes')",
            {
                "token_hash": "b" * 64,
                "user_id": user_a,
                "tenant_id": tenant_a,
                "requester_id": user_b,
            },
        ),
        (
            "INSERT INTO support_access_grants "
            "(id, tenant_id, support_user_id, granted_by_user_id, scope, reason, "
            "created_at, expires_at) "
            "VALUES ('00000000-0000-0000-0000-000000000004', :tenant_id, "
            ":support_user_id, :grantor_id, 'read_only', 'Unsafe synthetic grant', "
            "now(), now() + interval '1 hour')",
            {
                "tenant_id": tenant_a,
                "support_user_id": user_b,
                "grantor_id": user_b,
            },
        ),
    ]
    for statement, parameters in unsafe_statements:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement), parameters)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('AUTH-CONSTRAINT-USER-A@example.com', 'synthetic-hash', "
                "'Duplicate Auth User', 'pharmacist', :tenant_id, TRUE)"
            ),
            {"tenant_id": tenant_a},
        )

    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    assert {
        "auth_sessions",
        "login_throttles",
        "password_reset_tokens",
        "email_change_tokens",
        "support_access_grants",
        "support_access_audits",
    }.issubset(tables)
    engine.dispose()


@pytest.mark.integration
def test_auth_migration_fails_closed_on_case_insensitive_duplicate_emails(
    empty_postgresql_database_url: str,
) -> None:
    database_url = empty_postgresql_database_url
    baseline = _run_alembic(database_url, "upgrade", "6b4d8f2a9c31")
    assert baseline.returncode == 0, baseline.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Duplicate Email Tenant', 'duplicate-owner@example.com', FALSE) "
                "RETURNING id"
            )
        )
        for email in ("duplicate@example.com", "DUPLICATE@example.com"):
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(email, hashed_password, full_name, role, tenant_id, is_active) "
                    "VALUES (:email, 'synthetic-hash', 'Duplicate User', "
                    "'pharmacist', :tenant_id, TRUE)"
                ),
                {"email": email, "tenant_id": tenant_id},
            )
    engine.dispose()

    result = _run_alembic(database_url, "upgrade", "head")

    assert result.returncode != 0
    assert "Case-insensitive duplicate user emails detected" in result.stderr

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "6b4d8f2a9c31"
        assert "auth_sessions" not in inspect(connection).get_table_names()
    engine.dispose()
