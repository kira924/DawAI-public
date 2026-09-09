"""Add explicit tenant ownership and transfer history.

Revision ID: 3f2a9c7d1e84
Revises: c7d9e2f4a1b6
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f2a9c7d1e84"
down_revision: str | Sequence[str] | None = "c7d9e2f4a1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add constrained ownership and backfill the approved dummy data."""
    op.drop_index("ix_tenants_owner_email", table_name="tenants")
    op.create_index("ix_tenants_owner_email", "tenants", ["owner_email"], unique=False)

    op.create_unique_constraint("uq_users_id_tenant_id", "users", ["id", "tenant_id"])
    op.add_column("tenants", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_tenants_owner_user_id", "tenants", ["owner_user_id"], unique=False)
    op.create_foreign_key(
        "fk_tenants_owner_same_tenant",
        "tenants",
        "users",
        ["owner_user_id", "id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        CREATE TEMPORARY TABLE tenant_owner_backfill ON COMMIT DROP AS
        SELECT tenant_id, user_id, email
        FROM (
            SELECT
                tenant.id AS tenant_id,
                app_user.id AS user_id,
                app_user.email AS email,
                ROW_NUMBER() OVER (
                    PARTITION BY tenant.id
                    ORDER BY RANDOM()
                ) AS candidate_rank
            FROM tenants AS tenant
            JOIN users AS app_user ON app_user.tenant_id = tenant.id
            WHERE app_user.role <> 'super_admin'
        ) AS candidates
        WHERE candidate_rank = 1
        """
    )
    op.execute(
        """
        UPDATE users AS app_user
        SET role = 'manager',
            is_active = TRUE
        FROM tenant_owner_backfill AS selected
        WHERE app_user.id = selected.user_id
          AND app_user.tenant_id = selected.tenant_id
        """
    )
    op.execute(
        """
        UPDATE tenants AS tenant
        SET owner_user_id = selected.user_id,
            owner_email = selected.email
        FROM tenant_owner_backfill AS selected
        WHERE tenant.id = selected.tenant_id
        """
    )
    op.execute("UPDATE tenants SET is_active = FALSE WHERE owner_user_id IS NULL")
    op.execute("UPDATE tenants SET is_active = FALSE WHERE is_active IS NULL")

    op.alter_column(
        "tenants",
        "is_active",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    op.create_check_constraint(
        "ck_tenants_active_requires_owner",
        "tenants",
        "is_active IS NOT TRUE OR owner_user_id IS NOT NULL",
    )

    op.create_table(
        "tenant_ownership_transfers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("previous_owner_user_id", sa.Integer(), nullable=True),
        sa.Column("new_owner_user_id", sa.Integer(), nullable=False),
        sa.Column("performed_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["new_owner_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_ownership_transfers_new_owner_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["performed_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_owner_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_ownership_transfers_previous_owner_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ownership_transfers_tenant_created_at",
        "tenant_ownership_transfers",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_ownership_transfers_id",
        "tenant_ownership_transfers",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove ownership structures while retaining reconciled dummy values."""
    op.drop_index(
        "ix_tenant_ownership_transfers_id",
        table_name="tenant_ownership_transfers",
    )
    op.drop_index(
        "ix_ownership_transfers_tenant_created_at",
        table_name="tenant_ownership_transfers",
    )
    op.drop_table("tenant_ownership_transfers")

    op.drop_constraint("ck_tenants_active_requires_owner", "tenants", type_="check")
    op.drop_constraint("fk_tenants_owner_same_tenant", "tenants", type_="foreignkey")
    op.drop_index("ix_tenants_owner_user_id", table_name="tenants")
    op.drop_column("tenants", "owner_user_id")
    op.drop_constraint("uq_users_id_tenant_id", "users", type_="unique")

    op.alter_column(
        "tenants",
        "is_active",
        existing_type=sa.Boolean(),
        nullable=True,
        server_default=None,
    )
    op.drop_index("ix_tenants_owner_email", table_name="tenants")
    op.create_index("ix_tenants_owner_email", "tenants", ["owner_email"], unique=True)
