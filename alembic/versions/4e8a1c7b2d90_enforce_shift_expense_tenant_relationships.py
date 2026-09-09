"""close authentication lifecycle and tenant relationship gaps

Revision ID: 4e8a1c7b2d90
Revises: 6b4d8f2a9c31
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4e8a1c7b2d90"
down_revision: str | Sequence[str] | None = "6b4d8f2a9c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_foreign_keys_for_columns(table_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    expected_columns = tuple(columns)
    for foreign_key in inspector.get_foreign_keys(table_name):
        if tuple(foreign_key["constrained_columns"]) == expected_columns:
            constraint_name = foreign_key.get("name")
            if constraint_name is None:
                raise RuntimeError(f"Unnamed foreign key found on {table_name}{expected_columns}")
            op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def _assert_safe_legacy_relationships() -> None:
    connection = op.get_bind()
    shift_mismatches = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM shifts AS shift_record
            JOIN users AS user_record ON user_record.id = shift_record.user_id
            WHERE user_record.tenant_id <> shift_record.tenant_id
            """
        )
    )
    expense_shift_mismatches = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM expenses AS expense
            JOIN shifts AS shift_record ON shift_record.id = expense.shift_id
            WHERE shift_record.user_id <> expense.user_id
               OR shift_record.tenant_id <> expense.tenant_id
            """
        )
    )
    expense_user_mismatches = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM expenses AS expense
            JOIN users AS user_record ON user_record.id = expense.user_id
            WHERE user_record.tenant_id <> expense.tenant_id
            """
        )
    )

    if shift_mismatches or expense_shift_mismatches or expense_user_mismatches:
        raise RuntimeError(
            "Unsafe shift or expense tenant relationships detected: "
            f"shift-user={shift_mismatches}, "
            f"expense-shift-user={expense_shift_mismatches}, "
            f"expense-user={expense_user_mismatches}"
        )

    duplicate_normalized_emails = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT lower(email)
                FROM users
                GROUP BY lower(email)
                HAVING COUNT(*) > 1
            ) AS duplicate_emails
            """
        )
    )
    if duplicate_normalized_emails:
        raise RuntimeError(
            "Case-insensitive duplicate user emails detected: "
            f"groups={duplicate_normalized_emails}"
        )


def upgrade() -> None:
    _assert_safe_legacy_relationships()

    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    _drop_foreign_keys_for_columns("shifts", ["user_id"])
    _drop_foreign_keys_for_columns("expenses", ["shift_id"])
    _drop_foreign_keys_for_columns("expenses", ["user_id"])

    op.create_foreign_key(
        "fk_shifts_user_same_tenant",
        "shifts",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_session_id", sa.String(length=36), nullable=True),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry_order"),
        sa.ForeignKeyConstraint(
            ["replaced_by_session_id"],
            ["auth_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_auth_sessions_user_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    op.create_index(
        "ix_auth_sessions_user_active",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "login_throttles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key_type", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name="ck_login_throttles_failures_nonnegative",
        ),
        sa.CheckConstraint(
            "key_type IN ('account', 'ip')",
            name="ck_login_throttles_key_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_type", "key_hash", name="uq_login_throttles_key"),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_password_reset_tokens_expiry_order",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_password_reset_tokens_requester_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_password_reset_tokens_user_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_password_reset_tokens_user_active",
        "password_reset_tokens",
        ["user_id", "used_at", "expires_at"],
    )

    op.create_table(
        "email_change_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("new_email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_email_change_tokens_expiry_order",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_email_change_tokens_user_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_email_change_tokens_user_active",
        "email_change_tokens",
        ["user_id", "used_at", "expires_at"],
    )

    op.create_table(
        "support_access_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("support_user_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_support_access_grants_expiry_order",
        ),
        sa.CheckConstraint(
            "scope IN ('read_only', 'operations')",
            name="ck_support_access_grants_scope",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_support_access_grants_grantor_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["support_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_access_grants_active",
        "support_access_grants",
        ["support_user_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "support_access_audits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("support_grant_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("support_user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("request_method", sa.String(length=16), nullable=True),
        sa.Column("request_path", sa.String(length=512), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["support_grant_id"],
            ["support_access_grants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["support_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_access_audits_grant_occurred",
        "support_access_audits",
        ["support_grant_id", "occurred_at"],
    )
    op.create_index(
        "ix_support_access_audits_tenant_occurred",
        "support_access_audits",
        ["tenant_id", "occurred_at"],
    )
    op.create_foreign_key(
        "fk_expenses_shift_user_same_tenant",
        "expenses",
        "shifts",
        ["shift_id", "user_id", "tenant_id"],
        ["id", "user_id", "tenant_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_expenses_user_same_tenant",
        "expenses",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_support_access_audits_tenant_occurred",
        table_name="support_access_audits",
    )
    op.drop_index(
        "ix_support_access_audits_grant_occurred",
        table_name="support_access_audits",
    )
    op.drop_table("support_access_audits")
    op.drop_index("ix_support_access_grants_active", table_name="support_access_grants")
    op.drop_table("support_access_grants")
    op.drop_index("ix_email_change_tokens_user_active", table_name="email_change_tokens")
    op.drop_table("email_change_tokens")
    op.drop_index("ix_password_reset_tokens_user_active", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_table("login_throttles")
    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_family_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_constraint("fk_expenses_user_same_tenant", "expenses", type_="foreignkey")
    op.drop_constraint("fk_expenses_shift_user_same_tenant", "expenses", type_="foreignkey")
    op.drop_constraint("fk_shifts_user_same_tenant", "shifts", type_="foreignkey")

    op.create_foreign_key(
        "shifts_user_id_fkey",
        "shifts",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_index("uq_users_email_lower", table_name="users")
    op.create_foreign_key(
        "expenses_shift_id_fkey",
        "expenses",
        "shifts",
        ["shift_id"],
        ["id"],
    )
    op.create_foreign_key(
        "expenses_user_id_fkey",
        "expenses",
        "users",
        ["user_id"],
        ["id"],
    )
