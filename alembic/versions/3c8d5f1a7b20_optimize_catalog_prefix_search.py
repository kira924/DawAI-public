"""optimize catalog prefix search

Revision ID: 3c8d5f1a7b20
Revises: 1a6c9e4b2f70
Create Date: 2026-09-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3c8d5f1a7b20"
down_revision: str | Sequence[str] | None = "1a6c9e4b2f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_catalog_aliases_normalized_prefix",
        "catalog_product_aliases",
        ["normalized_alias", "catalog_product_id"],
        postgresql_ops={"normalized_alias": "text_pattern_ops"},
    )
    op.create_index(
        "ix_product_aliases_tenant_normalized_prefix",
        "product_aliases",
        ["tenant_id", "normalized_alias", "product_id"],
        postgresql_ops={"normalized_alias": "text_pattern_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_aliases_tenant_normalized_prefix",
        table_name="product_aliases",
    )
    op.drop_index(
        "ix_catalog_aliases_normalized_prefix",
        table_name="catalog_product_aliases",
    )
